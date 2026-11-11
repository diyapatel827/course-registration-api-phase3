from fastapi.testclient import TestClient
import main

client = TestClient(main.app)

CATALOG = '''<table><thead><tr><th>Course Code</th><th>Title</th><th>Credits</th><th>Prerequisites</th><th>Cross-listed</th></tr></thead><tbody>
<tr><td>COSC 3001</td><td>Capstone</td><td>3</td><td>COSC-2001</td><td>ITEC-3001</td></tr>
<tr><td>COSC-2001</td><td>Prerequisite</td><td>3</td><td>None</td><td></td></tr>
<tr><td>ITEC-3001</td><td>Same course</td><td>3</td><td>None</td><td>COSC 3001</td></tr>
<tr><td>COSC-1001</td><td>Intro</td><td>3</td><td>None</td><td>None</td></tr>
</tbody></table>'''

TRANSCRIPT = '''<table><thead><tr><th>Status</th><th>Course</th><th></th><th>Grade</th><th>Term</th><th>Credits</th></tr></thead><tbody>
<tr><td>Attempted</td><td>COSC-1001</td><td>Intro fail</td><td>40</td><td>24W</td><td>3</td></tr>
<tr><td>Completed</td><td>COSC-1001</td><td>Intro pass</td><td>70</td><td>24F</td><td>3</td></tr>
<tr><td>Completed</td><td>ITEC 3001</td><td>Same course</td><td>80</td><td>25W</td><td>3</td></tr>
<tr><td>Completed</td><td>COSC-2001</td><td>Prerequisite</td><td>75</td><td>26F</td><td>3</td></tr>
</tbody></table>'''


def setup_student(sid="test-1"):
    main.catalog.clear()
    main.students.clear()
    response = client.post("/api/v1/admin/catalog/import", files={"file": ("catalog.html", CATALOG, "text/html")})
    assert response.status_code == 201
    response = client.post(f"/api/v1/students/{sid}/history/import", files={"file": ("transcript.html", TRANSCRIPT, "text/html")})
    assert response.status_code == 201
    return sid


def test_root_health_and_missing_student():
    assert client.get("/").json()["status"] == "ok"
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/students/unknown/profile").status_code == 404


def test_catalog_import_lookup_and_normalization():
    main.catalog.clear()
    r = client.post("/api/v1/admin/catalog/import", files={"file": ("c.html", CATALOG, "text/html")})
    assert r.status_code == 201 and r.json()["courses_imported"] == 4
    r = client.get("/api/v1/catalog/courses/cosc3001")
    assert r.status_code == 200
    assert r.json()["course_code"] == "COSC 3001"
    assert r.json()["prerequisites"] == ["COSC-2001"]
    assert client.get("/api/v1/catalog/courses/NOPE9999").status_code == 404


def test_catalog_rejects_unrecognized_html():
    r = client.post("/api/v1/admin/catalog/import", files={"file": ("bad.html", "<p>nothing</p>", "text/html")})
    assert r.status_code == 400


def test_transcript_parser_and_grade_selection():
    parsed = main.parse_transcript(TRANSCRIPT)
    assert len(parsed) == 4
    assert any(x["course_code"] == "COSC-1001" and x["term"] == "24F" for x in parsed)
    assert main._credits_int("3 Credits") == 3
    assert main._credits_int("No credits") == 0
    assert main._grade_rank("A") > main._grade_rank("P")
    assert main._grade_rank("72") > main._grade_rank("A")


def test_audit_prerequisite_requires_strictly_earlier_term_and_ordering():
    sid = setup_student()
    client.post(f"/api/v1/students/{sid}/plan", json={"planned_courses": [
        {"course_code": "COSC-3001", "term": "26F"},
        {"course_code": "COSC-3001", "term": "26SP"},
    ]})
    report = client.get(f"/api/v1/students/{sid}/audit-report").json()
    # COSC-2001 is completed in 26F, so neither 26SP nor 26F has an earlier pass.
    assert [x["term"] for x in report["timeline_validation"]] == ["26SP", "26F"]
    assert all(x["errors"][0]["type"] == "MISSING_PREREQUISITE" for x in report["timeline_validation"])


def test_audit_strict_cross_list_credits_and_profile_mutations():
    sid = setup_student("student-2")
    client.post(f"/api/v1/students/{sid}/plan", json={"planned_courses": [
        {"course_code": "COSC3001", "term": "27W"},
        {"course_code": "COSC-2001", "term": "27W"},
    ]})
    lax = client.get(f"/api/v1/students/{sid}/audit-report?strict=false").json()
    strict = client.get(f"/api/v1/students/{sid}/audit-report?strict=true").json()
    assert lax["status"] == "warning"
    assert strict["status"] == "failed"
    assert lax["cross_list_violations"][0]["type"] == "CROSS_LIST_CONFLICT"
    assert lax["credit_summary"] == {
        "total_earned": 9,
        "total_planned": 6,
        "total_remaining_for_graduation": 105,
    }
    assert client.get(f"/api/v1/students/{sid}/profile").status_code == 200
    assert client.put(f"/api/v1/students/{sid}/history", json={"history": []}).status_code == 200
    assert client.put(f"/api/v1/students/{sid}/plan", json={"planned_courses": []}).status_code == 200
    assert client.delete(f"/api/v1/students/{sid}/history").status_code == 200
    assert client.delete(f"/api/v1/students/{sid}/plan").status_code == 200
    assert client.get(f"/api/v1/students/{sid}/audit-report").json()["status"] == "ok"


def test_no_issues_status_ok_and_term_parser():
    sid = setup_student("student-3")
    client.post(f"/api/v1/students/{sid}/plan", json={"planned_courses": []})
    report = client.get(f"/api/v1/students/{sid}/audit-report").json()
    assert report["status"] == "ok"
    assert main._term_key("23F") < main._term_key("24W") < main._term_key("24SP") < main._term_key("24S") < main._term_key("24F")
    assert main.normalize_code(" cosc-3001 ") == "COSC3001"
    assert main._extract_codes("None") == []
    assert main._extract_codes("COSC-2001, ITEC 3001") == ["COSC-2001", "ITEC 3001"]

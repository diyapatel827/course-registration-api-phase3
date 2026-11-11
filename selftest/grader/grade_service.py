"""COSC-3506 Phase 3 service grader (URL submission).

Sets up a known catalog + transcript + plan on the student's deployed API, then
scores the audit-report endpoint against an engineered scenario:
  - planned COSC-4426 needs COSC-3127 (not completed) -> MISSING_PREREQUISITE
  - planned ITEC-3506 is cross-listed with completed COSC-3506 -> cross-list violation
  - credit summary (target 120 credits)
  - ?strict=true flips status warning -> failed
Section-3 CI/CD is graded by presence of uploaded proof files (manually verified).
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from grader._svc import load_base_url, wake, Grader, emit, emit_unreachable, diag
except ImportError:
    from lib.vpl_service_runtime import load_base_url, wake, Grader, emit, emit_unreachable, diag

import httpx
from bs4 import BeautifulSoup

PAST = {"Completed", "In-Progress", "Attempted"}
PLAN = {"planned_courses": [
    {"course_code": "COSC-4426", "term": "26F"},   # missing prereq COSC-3127
    {"course_code": "ITEC-3506", "term": "26F"},   # cross-listed with completed COSC-3506
    {"course_code": "COSC-2406", "term": "26F"},    # retake of an attempted course
]}
GRAD_TOTAL = 120
SID = "770001"


def norm(code: str) -> str:
    return re.sub(r"[\s\-]", "", (code or "")).upper()


def _grade_rank(g):
    g = (g or "").strip()
    if re.fullmatch(r"\d+(\.\d+)?", g):
        return (2, float(g))
    if re.fullmatch(r"[A-EF][+-]?", g, re.I):
        return (1, 0.0)
    return (0, 0.0)


def parse_transcript(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for t in soup.find_all("table"):
        ths = [th.get_text(strip=True) for th in t.find_all("th")]
        if not ("Status" in ths and "Course" in ths and "Credits" in ths):
            continue
        for r in t.find_all("tr"):
            tds = [c.get_text(" ", strip=True) for c in r.find_all("td")]
            if len(tds) < 6 or not tds[1]:
                continue
            status, course, grade, term, credits = tds[0], tds[1], tds[3], tds[4], tds[5]
            if status not in PAST or not term:
                continue
            try:
                ce = int(credits)
            except ValueError:
                ce = 0
            rows.append({"c": course, "t": term, "ce": ce, "s": status, "g": grade})
    best = {}
    for r in rows:
        k = (norm(r["c"]), r["t"])
        if k not in best or (_grade_rank(r["g"]), r["ce"]) > (_grade_rank(best[k]["g"]), best[k]["ce"]):
            best[k] = r
    return list(best.values())


def _flatten_codes(obj) -> str:
    return norm(str(obj))


def run(base: str, data_dir=None) -> dict:
    if data_dir is None:
        data_dir = Path(__file__).resolve().parent.parent / "data" / "hidden"
    data_dir = Path(data_dir)
    catalog_html = data_dir / "catalog_hidden.html"
    transcript_html = data_dir / "studentB_hidden.html"
    tx = parse_transcript(transcript_html.read_text(encoding="utf-8", errors="replace"))
    expected_earned = sum(r["ce"] for r in tx if r["s"] == "Completed")
    expected_planned = 9   # COSC-4426 + ITEC-3506 + COSC-2406, 3 cr each (all in catalog)
    expected_remaining = max(0, GRAD_TOTAL - expected_earned - expected_planned)

    c = httpx.Client(base_url=base, timeout=30, follow_redirects=True)
    g = Grader()

    # ---- setup on the student's API ----
    def setup():
        c.post("/api/v1/admin/catalog/import",
               files={"file": ("catalog.html", catalog_html.read_bytes(), "text/html")})
        c.post(f"/api/v1/students/{SID}/history/import",
               files={"file": ("t.html", transcript_html.read_bytes(), "text/html")})
        c.post(f"/api/v1/students/{SID}/plan", json=PLAN)
    setup()

    def get_audit(strict):
        r = c.get(f"/api/v1/students/{SID}/audit-report", params={"strict": str(strict).lower()})
        return r.status_code, (r.json() if r.status_code == 200 else {})

    sc_lax, lax = get_audit(False)
    sc_strict, strict = get_audit(True)

    # ---- A. schema + strict behavior (20) ----
    def group_a():
        keys = {"student_id", "status", "timeline_validation", "cross_list_violations", "credit_summary"}
        schema_ok = sc_lax == 200 and keys.issubset(set(lax.keys()))
        strict_ok = (str(lax.get("status")).lower() == "warning"
                     and str(strict.get("status")).lower() == "failed")
        failed = []
        if not schema_ok:
            if sc_lax != 200:
                failed.append(f"GET /api/v1/students/{{sid}}/audit-report -> {sc_lax}")
            else:
                failed.append("audit-report: required response fields missing")
        if not strict_ok:
            failed.append("strict=true: status flips from warning to failed")
        return 0.5 * int(schema_ok) + 0.5 * int(strict_ok), failed

    # ---- B. missing prerequisite (25) ----
    def group_b():
        found = False
        for entry in lax.get("timeline_validation", []):
            for err in entry.get("errors", []):
                if "COSC4426" in _flatten_codes(err) and "PREREQ" in _flatten_codes(err.get("type")):
                    found = True
        if found:
            return 1.0, []
        tv = lax.get("timeline_validation", [])
        observed = "no timeline_validation errors" if not tv else \
            "timeline_validation present but no PREREQUISITE error for COSC-4426"
        return 0.0, [diag("timeline_validation", observed,
                          "a PREREQUISITE-type error for planned COSC-4426")]

    # ---- C. cross-list violation (20) ----
    def group_c():
        raw = lax.get("cross_list_violations", [])
        blob = _flatten_codes(raw)
        found = ("ITEC3506" in blob) or ("COSC3506" in blob and "CROSS" in _flatten_codes(raw))
        if found:
            return 1.0, []
        observed = "empty cross_list_violations" if not raw else \
            "cross_list_violations present but not referencing ITEC-3506/COSC-3506"
        return 0.0, [diag("cross_list_violations", observed,
                          "a violation flagging planned ITEC-3506 against completed COSC-3506")]

    # ---- D. retake / no double-count: total_earned correct (15) ----
    def group_d():
        observed = lax.get("credit_summary", {}).get("total_earned", "<missing>")
        try:
            correct = int(observed) == expected_earned
        except (TypeError, ValueError):
            correct = False
        if correct:
            return 1.0, []
        return 0.0, [diag("credit_summary.total_earned", observed,
                          "an integer = sum of your Completed credits, counting each retaken course once")]

    # ---- E. credit summary planned + remaining (15) ----
    def group_e():
        cs = lax.get("credit_summary", {})
        obs_p = cs.get("total_planned", "<missing>")
        obs_r = cs.get("total_remaining_for_graduation", "<missing>")
        try:
            ok_p = int(obs_p) == expected_planned
        except (TypeError, ValueError):
            ok_p = False
        try:
            ok_r = int(obs_r) == expected_remaining
        except (TypeError, ValueError):
            ok_r = False
        hints = []
        if not ok_p:
            hints.append(diag("credit_summary.total_planned", obs_p,
                              "an integer = total credits of your planned courses that exist in the catalog"))
        if not ok_r:
            hints.append(diag("credit_summary.total_remaining_for_graduation", obs_r,
                              f"max(0, {GRAD_TOTAL} - total_earned - total_planned)"))
        return 0.5 * ok_p + 0.5 * ok_r, hints

    # ---- F. Section-3 proof files present (5) ----
    def group_f():
        sub = Path(submission_dir)
        png = (sub / "ci_proof.png").exists()
        logs = (sub / "ci_logs.txt").exists()
        hints = []
        if not png:
            hints.append(diag("ci_proof.png", "not found in submission",
                              "an uploaded screenshot named ci_proof.png"))
        if not logs:
            hints.append(diag("ci_logs.txt", "not found in submission",
                              "an uploaded log file named ci_logs.txt"))
        return 0.5 * png + 0.5 * logs, hints

    g.check("A_audit_schema_strict", 20, group_a)
    g.check("B_missing_prerequisite", 25, group_b)
    g.check("C_cross_list", 20, group_c)
    g.check("D_retake_credits", 15, group_d)
    g.check("E_credit_summary", 15, group_e)
    g.check("F_ci_proof_present", 5, group_f)
    return g.result()


submission_dir = "."


def main():
    global submission_dir
    ap = argparse.ArgumentParser()
    ap.add_argument("submission")
    ap.add_argument("--data", required=True)
    a = ap.parse_args()
    submission_dir = a.submission
    base = load_base_url(a.submission)
    if not wake(base, "/", timeout=90):
        emit_unreachable()
        return
    emit(run(base, Path(a.data)))


if __name__ == "__main__":
    main()

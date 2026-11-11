from collections import OrderedDict
from typing import Dict, List, Optional, Tuple
import re

from bs4 import BeautifulSoup
from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

app = FastAPI(title="Student Academic History API", version="3.0.0")

# In-memory stores are intentionally isolated by student ID.
students: Dict[str, Dict[str, list]] = {}
catalog: Dict[str, dict] = {}


class HistoryCourse(BaseModel):
    course_code: str
    term: str
    credits_earned: int = 0
    status: str


class HistoryUpdate(BaseModel):
    history: List[HistoryCourse] = Field(default_factory=list)


class PlannedCourse(BaseModel):
    course_code: str
    term: str


class PlanUpdate(BaseModel):
    planned_courses: List[PlannedCourse] = Field(default_factory=list)


def _norm(value: str) -> str:
    """Normalize whitespace without changing the display spelling."""
    return " ".join(str(value or "").replace("\xa0", " ").split()).strip()


def normalize_code(value: str) -> str:
    """Course-code comparison is case-insensitive and ignores spaces/hyphens."""
    return re.sub(r"[\s-]", "", _norm(value)).upper()


def _extract_course_code(value: str) -> str:
    value = _norm(value)
    # Accept common catalog/transcript forms: COSC 3506, COSC-3506,
    # COSC3506, and transcript section suffixes such as COSC-3506-S01.
    match = re.match(r"^([A-Za-z]{2,}\s*-?\s*\d{4}(?:-[A-Za-z0-9]+)*)", value)
    return _norm(match.group(1)) if match else value


def _extract_codes(value: str) -> List[str]:
    value = _norm(value)
    if not value or value.lower() in {"none", "n/a", "na", "-", "—"}:
        return []
    pattern = re.compile(r"[A-Za-z]{2,}\s*-?\s*\d{4}(?:-[A-Za-z0-9]+)*")
    found = [_norm(m.group(0)) for m in pattern.finditer(value)]
    # Keep order while removing duplicate normalized codes.
    result, seen = [], set()
    for code in found:
        key = normalize_code(code)
        if key and key not in seen:
            result.append(code)
            seen.add(key)
    return result


def _grade_rank(grade: str) -> int:
    grade = _norm(grade)
    if not grade:
        return 0
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", grade):
        return 3
    if re.fullmatch(r"[A-Za-z][+-]?", grade) and grade.upper() != "P":
        return 2
    if grade.upper() == "P":
        return 1
    return 1


def _credits_int(value: str) -> int:
    match = re.search(r"-?\d+", _norm(value))
    return int(match.group(0)) if match else 0


def parse_transcript(html: str) -> List[dict]:
    soup = BeautifulSoup(html, "html.parser")
    selected = OrderedDict()

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        header_idx: Optional[int] = None
        col_map: Dict[str, int] = {}
        for i, row in enumerate(rows):
            cells = row.find_all(["th", "td"], recursive=False)
            labels = [_norm(c.get_text(" ", strip=True)).lower() for c in cells]
            indexes = {
                "status": next((j for j, x in enumerate(labels) if x == "status"), None),
                "course": next((j for j, x in enumerate(labels) if x == "course"), None),
                "term": next((j for j, x in enumerate(labels) if x == "term"), None),
                "credits": next((j for j, x in enumerate(labels) if x == "credits"), None),
                "grade": next((j for j, x in enumerate(labels) if x == "grade"), None),
            }
            if all(v is not None for v in indexes.values()):
                header_idx, col_map = i, indexes
                break
        if header_idx is None:
            continue

        for row in rows[header_idx + 1:]:
            cells = row.find_all(["th", "td"], recursive=False)
            if len(cells) <= max(col_map.values()):
                continue
            values = [_norm(c.get_text(" ", strip=True)) for c in cells]
            status = values[col_map["status"]]
            term = values[col_map["term"]]
            raw_code = values[col_map["course"]]
            if status not in {"Completed", "In-Progress", "Attempted"} or not term or not raw_code:
                continue
            course_code = _extract_course_code(raw_code)
            credits = _credits_int(values[col_map["credits"]])
            grade = values[col_map["grade"]]
            record = {"course_code": course_code, "term": term,
                      "credits_earned": credits, "status": status}
            key = (normalize_code(course_code), term)
            rank = (_grade_rank(grade), credits)
            old = selected.get(key)
            if old is None or rank > old["_rank"]:
                selected[key] = {"record": record, "_rank": rank}
    return [item["record"] for item in selected.values()]


def parse_catalog(html: str) -> Dict[str, dict]:
    soup = BeautifulSoup(html, "html.parser")
    parsed: Dict[str, dict] = {}
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        header_idx = None
        col_map = {}
        for i, row in enumerate(rows):
            cells = row.find_all(["th", "td"], recursive=False)
            labels = [_norm(c.get_text(" ", strip=True)).lower() for c in cells]
            code_i = next(
                (j for j, x in enumerate(labels) if "course code" in x or x == "code"), None
            )
            title_i = next(
                (j for j, x in enumerate(labels) if x == "title" or "course title" in x), None
            )
            credits_i = next((j for j, x in enumerate(labels) if "credit" in x), None)
            prereq_i = next((j for j, x in enumerate(labels) if "prerequisite" in x), None)
            cross_i = next(
                (j for j, x in enumerate(labels) if "cross-listed" in x or "cross listed" in x),
                None,
            )
            if None not in (code_i, title_i, credits_i, prereq_i, cross_i):
                header_idx = i
                col_map = {"code": code_i, "title": title_i, "credits": credits_i,
                           "prerequisites": prereq_i, "cross_listed": cross_i}
                break
        if header_idx is None:
            continue
        for row in rows[header_idx + 1:]:
            cells = row.find_all(["th", "td"], recursive=False)
            if len(cells) <= max(col_map.values()):
                continue
            values = [_norm(c.get_text(" ", strip=True)) for c in cells]
            display_code = _extract_course_code(values[col_map["code"]])
            key = normalize_code(display_code)
            if not key:
                continue
            parsed[key] = {
                "course_code": display_code,
                "title": values[col_map["title"]],
                "credits": _credits_int(values[col_map["credits"]]),
                "prerequisites": _extract_codes(values[col_map["prerequisites"]]),
                "cross_listed": _extract_codes(values[col_map["cross_listed"]]),
            }
    return parsed


def _ensure_student(student_id: str) -> dict:
    if student_id not in students:
        raise HTTPException(status_code=404, detail="Student not found")
    return students[student_id]


def _term_key(term: str) -> Tuple[int, int, str]:
    match = re.fullmatch(r"\s*(\d{2})(SP|W|S|F)\s*", str(term).upper())
    if not match:
        return (999, 999, str(term))
    year, season = int(match.group(1)), match.group(2)
    order = {"W": 0, "SP": 1, "S": 2, "F": 3}
    return (year, order[season], str(term))


def _audit(student_id: str, student: dict, strict: bool) -> dict:
    history = student.get("history", [])
    plan = student.get("plan", [])
    completed_by_code: Dict[str, List[dict]] = {}
    for item in history:
        if item.get("status") == "Completed":
            completed_key = normalize_code(item.get("course_code", ""))
            completed_by_code.setdefault(completed_key, []).append(item)

    errors_by_term: Dict[str, List[dict]] = {}
    cross_violations = []
    planned_total = 0
    planned_seen = set()

    for planned in plan:
        code = _norm(planned.get("course_code", ""))
        key = normalize_code(code)
        term = _norm(planned.get("term", ""))
        course = catalog.get(key)
        if not course:
            continue
        if key not in planned_seen:
            planned_total += int(course.get("credits", 0))
            planned_seen.add(key)

        for prerequisite in course.get("prerequisites", []):
            prereq_key = normalize_code(prerequisite)
            has_prior_completion = any(
                _term_key(h.get("term", "")) < _term_key(term)
                for h in completed_by_code.get(prereq_key, [])
            )
            if not has_prior_completion:
                errors_by_term.setdefault(term, []).append({
                    "course_code": code,
                    "type": "MISSING_PREREQUISITE",
                    "message": f"Missing prerequisite: {prerequisite}",
                })

        for cross_code in course.get("cross_listed", []):
            completed_matches = completed_by_code.get(normalize_code(cross_code), [])
            if completed_matches:
                completed_display = completed_matches[0].get("course_code", cross_code)
                cross_violations.append({
                    "course_code": code,
                    "type": "CROSS_LIST_CONFLICT",
                    "message": f"Cross-listed with completed course {completed_display}",
                })
                break

    # Count each distinct completed course once, regardless of repeated attempts.
    earned_by_code: Dict[str, int] = {}
    for item in history:
        if item.get("status") != "Completed":
            continue
        key = normalize_code(item.get("course_code", ""))
        if key:
            credits = int(item.get("credits_earned", 0) or 0)
            earned_by_code[key] = max(earned_by_code.get(key, 0), credits)
    total_earned = sum(earned_by_code.values())
    total_remaining = max(0, 120 - total_earned - planned_total)

    timeline = [
        {"term": term, "errors": errors_by_term[term]}
        for term in sorted(errors_by_term, key=_term_key)
    ]
    has_issues = bool(timeline or cross_violations)
    status = ("failed" if strict else "warning") if has_issues else "ok"
    return {
        "student_id": student_id,
        "status": status,
        "timeline_validation": timeline,
        "cross_list_violations": cross_violations,
        "credit_summary": {
            "total_earned": total_earned,
            "total_planned": planned_total,
            "total_remaining_for_graduation": total_remaining,
        },
    }


@app.get("/")
def root():
    return {"status": "ok", "service": "Student Academic History API"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/v1/admin/catalog/import", status_code=201)
async def import_catalog(file: UploadFile = File(...)):
    content = await file.read()
    imported = parse_catalog(content.decode("utf-8", errors="replace"))
    if not imported:
        raise HTTPException(status_code=400, detail="No catalog courses found")
    catalog.clear()
    catalog.update(imported)
    return {"status": "success", "courses_imported": len(imported)}


@app.get("/api/v1/catalog/courses/{course_code}")
def get_catalog_course(course_code: str):
    course = catalog.get(normalize_code(course_code))
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    return course


@app.post("/api/v1/students/{student_id}/history/import", status_code=201)
async def import_history(student_id: str, file: UploadFile = File(...)):
    content = await file.read()
    history = parse_transcript(content.decode("utf-8", errors="replace"))
    students[student_id] = {"history": history, "plan": []}
    return {"status": "success", "past_courses_imported": len(history)}


@app.put("/api/v1/students/{student_id}/history")
def update_history(student_id: str, body: HistoryUpdate):
    student = _ensure_student(student_id)
    student["history"] = [course.model_dump() for course in body.history]
    return {"status": "success", "message": "Academic history updated successfully"}


@app.delete("/api/v1/students/{student_id}/history")
def delete_history(student_id: str):
    student = _ensure_student(student_id)
    student["history"] = []
    return {"status": "success", "message": "Academic history cleared successfully"}


@app.post("/api/v1/students/{student_id}/plan")
def save_plan(student_id: str, body: PlanUpdate):
    student = _ensure_student(student_id)
    student["plan"] = [course.model_dump() for course in body.planned_courses]
    return {"status": "success", "planned_courses_saved": len(student["plan"])}


@app.put("/api/v1/students/{student_id}/plan")
def replace_plan(student_id: str, body: PlanUpdate):
    return save_plan(student_id, body)


@app.delete("/api/v1/students/{student_id}/plan")
def delete_plan(student_id: str):
    student = _ensure_student(student_id)
    student["plan"] = []
    return {"status": "success", "message": "Plan cleared successfully"}


@app.get("/api/v1/students/{student_id}/profile")
def get_profile(student_id: str):
    student = _ensure_student(student_id)
    return {"student_id": student_id, "history": student["history"], "plan": student["plan"]}


@app.get("/api/v1/students/{student_id}/audit-report")
def get_audit_report(student_id: str, strict: bool = False):
    student = _ensure_student(student_id)
    return _audit(student_id, student, strict)

# Student Academic History API — Phase 3

## Run locally

```bash
python -m venv venv
# Windows PowerShell: venv\Scripts\Activate.ps1
# macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Open `http://127.0.0.1:8000/docs` for interactive API documentation.

## Phase 1 catalog import

```bash
curl -X POST -F "file=@sample_catalog.html;type=text/html" http://localhost:8000/api/v1/admin/catalog/import
curl http://localhost:8000/api/v1/catalog/courses/COSC3506
```

## Phase 2 history and plan

```bash
curl -F "file=@student-example.html" http://localhost:8000/api/v1/students/111/history/import
curl http://localhost:8000/api/v1/students/111/profile
```

Save a plan with `POST /api/v1/students/{student_id}/plan` and JSON body:

```json
{"planned_courses":[{"course_code":"COSC-3506","term":"26F"}]}
```

## Phase 3 audit

`GET /api/v1/students/{student_id}/audit-report?strict=false`

Set `strict=true` to return `status: "failed"` whenever any prerequisite or cross-list issue exists. Without issues the status is `ok`.

## Tests

```bash
pytest -q --cov=main --cov-report=term-missing --cov-fail-under=80
uvicorn main:app --host 127.0.0.1 --port 8000
# In another terminal:
API_BASE_URL=http://127.0.0.1:8000 pytest -q tests/test_live_integration.py
```

The GitHub Actions workflow runs linting, tests with an 80% coverage gate, and live API integration tests.

## Render deployment

Build command: `pip install -r requirements.txt`

Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`

Submit only the deployed base URL in `api_url.txt` (no trailing slash or path).

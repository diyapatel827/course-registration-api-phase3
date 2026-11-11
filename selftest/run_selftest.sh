#!/bin/bash
# Self-test: runs the SAME auto-grader the server uses, against a VISIBLE practice
# dataset. Start your service on localhost:8000 first (or set API_URL).
# One-time setup:  pip install httpx beautifulsoup4
set -e
cd "$(dirname "$0")"
echo "${API_URL:-http://localhost:8000}" > api_url.txt
exec python3 - << 'PY'
import sys
sys.path.insert(0, ".")
try:
    from grader.grade_service import run
    from grader._svc import load_base_url, wake, format_report
except ModuleNotFoundError as e:
    print(f"Missing dependency '{e.name}'. Install the self-test requirements once:")
    print("    pip install httpx beautifulsoup4")
    sys.exit(1)
base = load_base_url(".")
if not wake(base, "/", timeout=20):
    print(f"Service unreachable at {base} — start it, then re-run.")
    sys.exit(1)
print(format_report(run(base, "./data/selftest")))
PY

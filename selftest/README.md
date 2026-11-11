# Self-test (practice grader)

This runs the **same** auto-grader the server uses, on a **practice** dataset you
can see — so you can find and fix problems locally before you submit (each extra
submission costs 10%).

## Use
1. Install the self-test dependencies (one time): `pip install httpx beautifulsoup4`
2. Start your service: `uvicorn main:app --port 8000`
3. From this folder: `bash run_selftest.sh`
   (or `API_URL=http://localhost:8000 bash run_selftest.sh`)
4. Read the table. Each failing line shows `observed … expected …` — the observed
   value is what *your* API returned; the expected text is the rule from the spec.

The practice data is **different** from the hidden grading data, so a passing
self-test means your logic is right, not that you memorized answers.

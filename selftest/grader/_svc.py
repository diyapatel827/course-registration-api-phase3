# lib/vpl_service_runtime.py
"""Runtime helpers for service (URL-submission) VPL graders.

Copied into each service bundle's grader/ dir. Stdlib + httpx only.
Usage in a bundle's grade_service.py:

    from grader._svc import load_base_url, wake, Grader, emit
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path
import httpx


def load_base_url(submission_dir, filename: str = "api_url.txt") -> str:
    p = Path(submission_dir) / filename
    url = p.read_text(encoding="utf-8", errors="replace").strip().splitlines()[0].strip()
    return url.rstrip("/")


def wake(base_url: str, health_path: str = "/", timeout: int = 90) -> bool:
    """Poll health_path until 200 or timeout. Tolerates Render cold-start."""
    deadline = time.time() + timeout
    url = base_url.rstrip("/") + "/" + health_path.lstrip("/")
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=10, follow_redirects=True).status_code < 500:
                return True
        except Exception:
            pass
        time.sleep(3)
    return False


class Grader:
    """Score accumulator. check() funcs return True/False or a 0..1 ramp."""

    def __init__(self):
        self.score = 0
        self.detail: dict[str, dict] = {}

    def check(self, name: str, points: int, fn) -> None:
        try:
            val = fn()
            if isinstance(val, dict):
                # {check_name: bool} — equal-weight, failed names reported
                failed = [k for k, v in val.items() if not v]
                frac = sum(bool(v) for v in val.values()) / len(val) if val else 0.0
            elif isinstance(val, tuple):
                # (float_score, [failed_hint, ...]) — custom weighting with hints
                frac, failed = float(val[0]), list(val[1])
            else:
                failed = []
                frac = 1.0 if val is True else (0.0 if val is False else float(val))
            frac = max(0.0, min(1.0, frac))
        except Exception as e:  # never crash on a misbehaving student API
            self.detail[name] = {"points": 0, "max": points, "error": repr(e)}
            return
        earned = round(points * frac)
        self.score += earned
        entry = {"points": earned, "max": points}
        if failed:
            entry["failed"] = failed
        self.detail[name] = entry

    def result(self, schema_ok: bool = True) -> dict:
        return {"schema_ok": schema_ok, "score": int(self.score), "detail": self.detail}


def emit(obj: dict) -> None:
    """Print exactly one JSON object on stdout (what the jail extracts)."""
    sys.stdout.write(json.dumps(obj))
    sys.stdout.write("\n")


def emit_unreachable() -> None:
    emit({"schema_ok": False, "score": 0, "error": "service unreachable"})


def diag(check: str, observed, expected) -> str:
    """Standardized failure hint: Tier-1 observed symptom + Tier-2 contract.
    NEVER pass a Tier-3 value (anything derived from the hidden dataset) as
    `expected` — state the contract in spec terms instead."""
    return f"{check}: observed {observed}, expected {expected}"


def format_report(result: dict) -> str:
    """Render the auto-grader feedback table. Used by both the jail eval.sh and
    the student self-test runner so local and server output are identical."""
    bar = "=" * 46
    lines = [bar, "  Auto-Grader Results", bar,
             f"  Total Score : {result.get('score', '?')} / 100"]
    if not result.get("schema_ok", True):
        lines.append("  WARNING: service returned unexpected data")
    lines.append("-" * 46)
    for key, d in result.get("detail", {}).items():
        pts = d.get("points", "?")
        mx = d.get("max", "?")
        err = f"  *** {d['error']}" if d.get("error") else ""
        lines.append(f"  {key:<28} {pts:>3}/{mx}{err}")
        for fc in d.get("failed", []):
            lines.append(f"      - {fc}")
    lines.append(bar)
    return "\n".join(lines)

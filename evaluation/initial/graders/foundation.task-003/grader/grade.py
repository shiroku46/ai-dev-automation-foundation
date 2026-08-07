from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

KEYS = (
    "AI_DEV_EVAL_TASK_ID",
    "AI_DEV_EVAL_TASK_VERSION",
    "AI_DEV_EVAL_MANIFEST_SHA256",
    "AI_DEV_EVAL_GRADER_SHA256",
    "AI_DEV_EVAL_FOUNDATION_SHA",
    "AI_DEV_EVAL_BASE_SHA",
    "AI_DEV_EVAL_CANDIDATE_SHA",
)


def arguments():
    if len(sys.argv) != 5 or sys.argv[1] != "--workspace" or sys.argv[3] != "--result":
        raise SystemExit(2)
    return Path(sys.argv[2]), Path(sys.argv[4])


def identity():
    value = {key: os.environ[key] for key in KEYS}
    return {
        "task_id": value["AI_DEV_EVAL_TASK_ID"],
        "task_version": int(value["AI_DEV_EVAL_TASK_VERSION"]),
        "manifest_sha256": value["AI_DEV_EVAL_MANIFEST_SHA256"],
        "grader_sha256": value["AI_DEV_EVAL_GRADER_SHA256"],
        "foundation_sha": value["AI_DEV_EVAL_FOUNDATION_SHA"],
        "base_sha": value["AI_DEV_EVAL_BASE_SHA"],
        "candidate_sha": value["AI_DEV_EVAL_CANDIDATE_SHA"],
    }


def unit_tests(workspace):
    return subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(workspace / "tests"), "-p", "test_*.py"],
        cwd=workspace,
        env={"PYTHONPATH": str(workspace), "PYTHONDONTWRITEBYTECODE": "1"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=False,
    ).returncode == 0


def finish(path, checks, passed_summary, failed_summary):
    checks = sorted(checks, key=lambda item: item["check_id"])
    outcome = "passed" if all(item["outcome"] == "passed" for item in checks) else "failed"
    raw = json.dumps(
        {"schema_version": 1, **identity(), "outcome": outcome, "checks": checks,
         "summary": passed_summary if outcome == "passed" else failed_summary},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(raw)
    os.replace(temporary, path)
    return 0 if outcome == "passed" else 1


workspace, result = arguments()
behavior = subprocess.run(
    [sys.executable, "-c",
     "from src.settings import retry_count;assert retry_count({'max_retries':5})==5;"
     "assert retry_count({'retries':5})==2;assert retry_count({})==2"],
    cwd=workspace,
    env={"PYTHONPATH": str(workspace), "PYTHONDONTWRITEBYTECODE": "1"},
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    timeout=30,
    check=False,
).returncode == 0
try:
    normalized = "".join((workspace / "tests/test_settings.py").read_text(encoding="utf-8").split())
    updated = '{"max_retries":5}' in normalized and '{"retries":5}' in normalized and "retry_count({})" in normalized
except (OSError, UnicodeError):
    updated = False
tests_ok = unit_tests(workspace)
raise SystemExit(finish(
    result,
    [
        {
            "check_id": "behavior",
            "outcome": "passed" if behavior else "failed",
            "message": "Retry-key behavior passed." if behavior else "Retry-key behavior failed.",
            "evidence_paths": ["src/settings.py"],
        },
        {
            "check_id": "test_update",
            "outcome": "passed" if updated and tests_ok else "failed",
            "message": "Updated retry tests passed." if updated and tests_ok else "Retry tests are not updated or are failing.",
            "evidence_paths": ["tests/test_settings.py"],
        },
    ],
    "Retry-key migration acceptance passed.",
    "Retry-key migration acceptance failed.",
))

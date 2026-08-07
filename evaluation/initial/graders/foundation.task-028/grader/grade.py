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


def finish(path, check_id, passed, passed_message, failed_message, evidence_paths, passed_summary, failed_summary):
    check = {
        "check_id": check_id,
        "outcome": "passed" if passed else "failed",
        "message": passed_message if passed else failed_message,
        "evidence_paths": evidence_paths,
    }
    outcome = "passed" if passed else "failed"
    raw = json.dumps(
        {"schema_version": 1, **identity(), "outcome": outcome, "checks": [check],
         "summary": passed_summary if passed else failed_summary},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(raw)
    os.replace(temporary, path)
    return 0 if passed else 1

workspace, result = arguments()
try:
    access_source = (workspace / "src/access.py").read_text(encoding="utf-8")
except (OSError, UnicodeError):
    access_source = ""
reuses_policy = "from src.policy import is_active" in access_source and "return is_active(state)" in access_source
tests_passed = subprocess.run(
    [sys.executable, "-m", "unittest", "discover", "-s", str(workspace / "tests"), "-p", "test_*.py"],
    cwd=workspace,
    env={"PYTHONPATH": str(workspace), "PYTHONDONTWRITEBYTECODE": "1"},
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    timeout=30,
    check=False,
).returncode == 0
passed = reuses_policy and tests_passed
raise SystemExit(finish(
    result,
    "shared_policy",
    passed,
    "Shared policy normalization and reuse passed.",
    "Shared policy normalization or reuse is incomplete.",
    ["src/policy.py", "src/access.py", "tests/test_access.py"],
    "Shared-policy acceptance passed.",
    "Shared-policy acceptance failed.",
))

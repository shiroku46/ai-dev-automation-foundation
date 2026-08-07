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


def finish(path, passed):
    check = {
        "check_id": "shared_abstraction",
        "outcome": "passed" if passed else "failed",
        "message": "Display path uses the shared normalization abstraction." if passed else "Shared normalization change is incomplete.",
        "evidence_paths": ["src/text.py", "src/display.py", "tests/test_display.py"],
    }
    outcome = "passed" if passed else "failed"
    raw = json.dumps(
        {"schema_version": 1, **identity(), "outcome": outcome, "checks": [check],
         "summary": "Multi-file abstraction acceptance passed." if passed else "Multi-file abstraction acceptance failed."},
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
    display_source = (workspace / "src/display.py").read_text(encoding="utf-8")
    shared = "from src.text import normalize" in display_source and "normalize(value)" in display_source
except (OSError, UnicodeError):
    shared = False
raise SystemExit(finish(result, shared and unit_tests(workspace)))

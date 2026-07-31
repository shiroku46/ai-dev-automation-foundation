#!/usr/bin/env python3
"""Validate required public foundation structure and workflow invariants."""
from __future__ import annotations
from pathlib import Path
import re
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    "README.md", "LICENSE", "SECURITY.md", "AGENTS.md", "CLAUDE.md",
    "scripts/public_export_guard.py", "scripts/ai_recovery_supervisor.py",
    "scripts/supervisor_policy.py", "bootstrap/generator.py",
    ".github/workflows/ci.yml", ".github/workflows/unit-tests.yml",
    ".github/workflows/claude-queue.yml", ".github/workflows/ci-reconcile.yml",
    ".github/workflows/supervisor.yml",
}
PIN = re.compile(r"uses:\s*[^@\s]+@[0-9a-f]{40}\s*$", re.M)

def fail(message: str):
    raise AssertionError(message)

def main() -> int:
    missing = sorted(path for path in REQUIRED if not (ROOT / path).is_file())
    if missing:
        fail(f"missing required files: {missing}")
    for workflow in sorted((ROOT / ".github/workflows").glob("*.yml")):
        text = workflow.read_text(encoding="utf-8")
        yaml.safe_load(text)
        if "pull_request_target" in text:
            fail(f"{workflow}: pull_request_target is prohibited")
        for line in text.splitlines():
            stripped = line.strip()
            if (stripped.startswith("uses:") or stripped.startswith("- uses:")) and not PIN.search(line):
                fail(f"{workflow}: action is not pinned to a commit: {stripped}")
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    if "permissions:\n  contents: read" not in ci or "secrets." in ci or "id-token: write" in ci:
        fail("fork CI must be read-only and secret-free")
    queue = (ROOT / ".github/workflows/claude-queue.yml").read_text()
    if "github.actor == vars.AUTOMATION_OWNER" not in queue:
        fail("queue owner authorization is missing")
    if "github.triggering_actor" in queue:
        fail("queue must not reject connected owner actions through triggering_actor")
    supervisor = (ROOT / ".github/workflows/supervisor.yml").read_text()
    if "ref: ${{ github.event.repository.default_branch }}" not in supervisor:
        fail("write-capable supervisor must checkout the default branch explicitly")
    print("repository validation: clean")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

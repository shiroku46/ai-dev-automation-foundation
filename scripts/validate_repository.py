#!/usr/bin/env python3
"""Validate required public foundation structure and workflow invariants."""
from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    "README.md",
    "LICENSE",
    "SECURITY.md",
    "AGENTS.md",
    "CLAUDE.md",
    "scripts/public_export_guard.py",
    "scripts/ai_recovery_supervisor.py",
    "scripts/supervisor_policy.py",
    "scripts/supervisor_runtime.py",
    "bootstrap/generator.py",
    ".github/workflows/ci.yml",
    ".github/workflows/unit-tests.yml",
    ".github/workflows/trusted-checks.yml",
    ".github/workflows/claude-queue.yml",
    ".github/workflows/ci-reconcile.yml",
    ".github/workflows/supervisor.yml",
}
PIN = re.compile(r"uses:\s*[^@\s]+@[0-9a-f]{40}\s*$")
LOCAL_REUSABLE = "uses: ./.github/workflows/trusted-checks.yml"


def fail(message: str) -> None:
    raise AssertionError(message)


def main() -> int:
    missing = sorted(path for path in REQUIRED if not (ROOT / path).is_file())
    if missing:
        fail(f"missing required files: {missing}")

    workflows = sorted((ROOT / ".github/workflows").glob("*.yml"))
    if not workflows:
        fail("no workflows found")
    for workflow in workflows:
        text = workflow.read_text(encoding="utf-8")
        if "\t" in text:
            fail(f"{workflow}: tab indentation is prohibited")
        if not text.startswith("name:") or "\non:\n" not in text or "\njobs:\n" not in text:
            fail(f"{workflow}: required top-level workflow sections are missing")
        if "pull_request_target" in text:
            fail(f"{workflow}: pull_request_target is prohibited")
        for line in text.splitlines():
            stripped = line.strip().removeprefix("- ")
            if stripped.startswith("uses:") and stripped != LOCAL_REUSABLE:
                if not PIN.search(line):
                    fail(f"{workflow}: action is not pinned to a commit: {stripped}")

    for name in ("ci.yml", "unit-tests.yml"):
        text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
        if "permissions:\n  contents: read" not in text:
            fail(f"{name}: contributor checks must be contents-read-only")
        if "secrets." in text or "id-token: write" in text or "checks: write" in text:
            fail(f"{name}: contributor checks must be Secret/OIDC/write-free")

    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    if "Psych.parse_stream" not in ci or "documents.length == 1" not in ci:
        fail("CI must parse the complete workflow YAML stream and require one document")

    trusted = (ROOT / ".github/workflows/trusted-checks.yml").read_text(encoding="utf-8")
    for required in (
        "workflow_call:",
        "CI / validate",
        "Unit Tests / test",
        "checks: write",
        "job.workflow_ref",
        "job.workflow_sha",
        "head_sha=$TARGET_SHA",
        "status=in_progress",
        "status=completed",
    ):
        if required not in trusted:
            fail(f"trusted-checks.yml: missing invariant {required!r}")
    authorize_block = trusted.split("  authorize:\n", 1)[1].split("  validate_target:\n", 1)[0]
    finalize_block = trusted.split("  finalize:\n", 1)[1]
    if "actions/checkout" in authorize_block or "actions/checkout" in finalize_block:
        fail("write-capable attestation jobs must not check out proposed code")
    for job_name in ("validate_target", "test_target"):
        block = trusted.split(f"  {job_name}:\n", 1)[1].split("\n  ", 1)[0]
        if "permissions:\n      contents: read" not in block:
            fail(f"{job_name}: proposed-code job must be contents-read-only")
        if "checks: write" in block or "id-token: write" in block or "secrets." in block:
            fail(f"{job_name}: proposed-code job has a prohibited capability")

    queue = (ROOT / ".github/workflows/claude-queue.yml").read_text(encoding="utf-8")
    for required in (
        "github.actor == github.repository_owner",
        "github.actor == vars.AUTOMATION_OWNER",
        "trusted_run_id:",
        ".github/workflows/supervisor.yml@",
    ):
        if required not in queue:
            fail(f"queue owner/supervisor authorization is missing {required!r}")
    if "github.triggering_actor" in queue:
        fail("queue must not reject connected owner actions through triggering_actor")

    reconcile = (ROOT / ".github/workflows/ci-reconcile.yml").read_text(encoding="utf-8")
    if LOCAL_REUSABLE not in reconcile:
        fail("reconciliation must call the fixed local reusable workflow")
    if "python -m scripts.supervisor_runtime discover" not in reconcile:
        fail("reconciliation must use bounded default-branch discovery")
    if "gh workflow run" in reconcile or "statuses: write" in reconcile:
        fail("reconciliation must not create orphan workflow/status evidence")

    supervisor = (ROOT / ".github/workflows/supervisor.yml").read_text(encoding="utf-8")
    if "ref: ${{ github.event.repository.default_branch }}" not in supervisor:
        fail("write-capable supervisor must checkout the default branch explicitly")
    if "pull_request:" in supervisor:
        fail("write-capable supervisor must not run directly as an untrusted PR workflow")

    runtime = (ROOT / "scripts/supervisor_runtime.py").read_text(encoding="utf-8")
    for required in (
        "referenced_workflows",
        "current_default_sha()",
        "external_id",
        "reviewThreads(first:100,after:$cursor)",
        "expected_marker",
        "MAX_ATTESTATION_ATTEMPTS",
    ):
        if required not in runtime:
            fail(f"supervisor runtime is missing trusted evidence invariant {required!r}")

    print("repository validation: clean")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

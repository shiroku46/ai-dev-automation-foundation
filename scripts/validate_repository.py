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


def fail(message: str) -> None:
    raise AssertionError(message)


def workflow(name: str) -> str:
    return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def job_block(text: str, job: str, next_job: str | None = None) -> str:
    marker = f"\n  {job}:\n"
    if marker not in text:
        fail(f"missing job {job}")
    block = text.split(marker, 1)[1]
    if next_job:
        boundary = f"\n  {next_job}:\n"
        if boundary not in block:
            fail(f"missing following job {next_job}")
        block = block.split(boundary, 1)[0]
    return block


def main() -> int:
    missing = sorted(path for path in REQUIRED if not (ROOT / path).is_file())
    if missing:
        fail(f"missing required files: {missing}")

    workflows = sorted((ROOT / ".github/workflows").glob("*.yml"))
    if not workflows:
        fail("no workflows found")
    for path in workflows:
        text = path.read_text(encoding="utf-8")
        if "\t" in text:
            fail(f"{path}: tab indentation is prohibited")
        if not text.startswith("name:") or "\non:\n" not in text or "\njobs:\n" not in text:
            fail(f"{path}: required top-level workflow sections are missing")
        if "pull_request_target" in text:
            fail(f"{path}: pull_request_target is prohibited")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped in {"uses: ./.github/workflows/trusted-checks.yml", "- uses: ./.github/workflows/trusted-checks.yml"}:
                continue
            if (stripped.startswith("uses:") or stripped.startswith("- uses:")) and not PIN.search(line):
                fail(f"{path}: action is not pinned to a commit: {stripped}")

    ci = workflow("ci.yml")
    unit = workflow("unit-tests.yml")
    for name, text in (("ci.yml", ci), ("unit-tests.yml", unit)):
        if "\n  pull_request:\n" not in text:
            fail(f"{name}: public Pull Request trigger is missing")
        if "workflow_dispatch" in text or "workflow_call" in text:
            fail(f"{name}: public validation must not expose a write-capable trusted trigger")
        if "permissions: {}" not in text or "permissions:\n      contents: read" not in text:
            fail(f"{name}: public jobs must be explicitly read-only")
        for forbidden in ("secrets.", "id-token: write", "checks: write", "statuses: write", "actions: write"):
            if forbidden in text:
                fail(f"{name}: public jobs contain forbidden capability {forbidden}")
    if "Psych.parse_stream" not in ci or "documents.length == 1" not in ci:
        fail("CI must parse the complete workflow YAML stream and require one document")

    trusted = workflow("trusted-checks.yml")
    if "\n  workflow_call:\n" not in trusted:
        fail("trusted checks must be a fixed reusable workflow")
    if "CI / validate" not in trusted or "Unit Tests / test" not in trusted:
        fail("trusted exact-SHA check names are missing")
    if "foundation:trusted-checks:" not in trusted:
        fail("trusted attestation external identity is missing")
    begin = job_block(trusted, "begin", "validate")
    validate = job_block(trusted, "validate", "test")
    test = job_block(trusted, "test", "finalize")
    finalize = job_block(trusted, "finalize")
    for name, block in (("begin", begin), ("finalize", finalize)):
        if "checks: write" not in block:
            fail(f"trusted {name} metadata job needs checks: write")
        if "actions/checkout@" in block or "scripts/" in block or "unittest" in block:
            fail(f"trusted {name} write job must not execute proposed-branch code")
    for name, block in (("validate", validate), ("test", test)):
        if "permissions:\n      contents: read" not in block:
            fail(f"trusted {name} execution job must be read-only")
        if "checks: write" in block or "id-token: write" in block or "secrets." in block:
            fail(f"trusted {name} execution job has write/secret capability")
        if "actions/checkout@" not in block or "ref: ${{ inputs.target_sha }}" not in block:
            fail(f"trusted {name} execution job is not bound to the immutable target")

    queue = workflow("claude-queue.yml")
    if "github.actor == github.repository_owner" not in queue:
        fail("queue repository-owner authorization is missing")
    if "github.actor == vars.AUTOMATION_OWNER" not in queue:
        fail("queue configured-owner authorization is missing")
    if "github.triggering_actor" in queue:
        fail("queue must not reject connected owner actions through triggering_actor")
    if 'body.strip() == trigger' not in queue:
        fail("queue exact standalone comment trigger is missing")
    if "source_run_id" not in queue or "actions/workflows/supervisor.yml" not in queue:
        fail("queue bot dispatch is not bound to a trusted supervisor run")

    reconcile = workflow("ci-reconcile.yml")
    if 'workflows: ["CI", "Unit Tests"]' not in reconcile:
        fail("reconciliation must begin only from fixed public workflows or schedule")
    if "uses: ./.github/workflows/trusted-checks.yml" not in reconcile:
        fail("reconciliation must call the fixed local trusted workflow")
    if "max_candidates = 10" not in reconcile or "max_attempts = 3" not in reconcile:
        fail("reconciliation bounds are missing")
    if "base.get(\"ref\") != default" not in reconcile:
        fail("reconciliation default-branch target gate is missing")
    if "gh workflow run" in reconcile or "statuses: write" in reconcile:
        fail("reconciliation must not select arbitrary workflows or publish orphan statuses")

    supervisor = workflow("supervisor.yml")
    if "\n  pull_request:\n" in supervisor:
        fail("write-capable supervisor must not load from a proposed Pull Request ref")
    if "ref: ${{ github.event.repository.default_branch }}" not in supervisor:
        fail("write-capable supervisor must checkout the default branch explicitly")
    if 'workflows: ["Trusted Exact-SHA Checks"]' not in supervisor:
        fail("supervisor must reconcile after fixed trusted checks")

    runtime = (ROOT / "scripts/supervisor_runtime.py").read_text(encoding="utf-8")
    for required in (
        "trusted-checks.yml",
        'run.get("event") != "workflow_call"',
        'run.get("head_branch") != DEFAULT_BRANCH',
        'base.get("ref") != DEFAULT_BRANCH',
        "foundation:trusted-checks:",
        "merge_method=squash",
        "f\"sha={sha}\"",
    ):
        if required not in runtime:
            fail(f"supervisor runtime invariant is missing: {required}")

    generator = (ROOT / "bootstrap/generator.py").read_text(encoding="utf-8")
    if '".github/workflows/trusted-checks.yml"' not in generator:
        fail("Bootstrap allowlist does not include trusted exact-SHA checks")

    print("repository validation: clean")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

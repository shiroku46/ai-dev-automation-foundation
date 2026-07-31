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
            fail(f"{path}: required top-level sections are missing")
        if "pull_request_target" in text:
            fail(f"{path}: pull_request_target is prohibited")
        for line in text.splitlines():
            stripped = line.strip().removeprefix("- ")
            if stripped.startswith("uses:") and not PIN.search(line):
                fail(f"{path}: action is not pinned to a commit: {stripped}")

    ci = workflow("ci.yml")
    unit = workflow("unit-tests.yml")
    for name, text in (("ci.yml", ci), ("unit-tests.yml", unit)):
        if "\n  pull_request:\n" not in text:
            fail(f"{name}: public Pull Request trigger is missing")
        if "permissions:\n  contents: read" not in text:
            fail(f"{name}: public workflow must be read-only")
        for forbidden in (
            "secrets.",
            "id-token: write",
            "checks: write",
            "statuses: write",
            "actions: write",
        ):
            if forbidden in text:
                fail(f"{name}: forbidden capability {forbidden}")
    if "Psych.parse_stream" not in ci or "documents.length == 1" not in ci:
        fail("CI must parse the complete single-document YAML stream")

    trusted = workflow("trusted-checks.yml")
    if "run-name: Trusted checks ${{ inputs.target_sha }}" not in trusted:
        fail("trusted workflow run title is not candidate-bound")
    if "\n  workflow_dispatch:\n" not in trusted or "workflow_call:" in trusted:
        fail("trusted checks must use the fixed default-branch dispatch path")
    if "WORKFLOW_REF: ${{ github.workflow_ref }}" not in trusted:
        fail("trusted checks do not verify their supported workflow identity")
    if "WORKFLOW_SHA: ${{ github.workflow_sha }}" not in trusted:
        fail("trusted checks do not verify the current workflow SHA")
    if "job.workflow_ref" in trusted or "job.workflow_sha" in trusted:
        fail("trusted checks use unsupported job context keys")
    if "CI / validate" not in trusted or "Unit Tests / test" not in trusted:
        fail("trusted exact-SHA check names are missing")
    authorize = job_block(trusted, "authorize", "validate_target")
    validate = job_block(trusted, "validate_target", "test_target")
    test = job_block(trusted, "test_target", "finalize")
    finalize = job_block(trusted, "finalize")
    for name, block in (("authorize", authorize), ("finalize", finalize)):
        if "checks: write" not in block:
            fail(f"trusted {name} metadata job needs checks: write")
        if "actions/checkout@" in block or "python scripts/" in block or "unittest" in block:
            fail(f"trusted {name} write job must not execute candidate code")
    for name, block in (("validate", validate), ("test", test)):
        if "permissions:\n      contents: read" not in block:
            fail(f"trusted {name} execution job must be read-only")
        if "checks: write" in block or "id-token: write" in block or "secrets." in block:
            fail(f"trusted {name} execution job has write/secret capability")
        if "actions/checkout@" not in block or "ref: ${{ env.TARGET_SHA }}" not in block:
            fail(f"trusted {name} job is not bound to the immutable candidate")

    queue = workflow("claude-queue.yml")
    if "github.actor == github.repository_owner" not in queue:
        fail("queue repository-owner authorization is missing")
    if "github.actor == vars.AUTOMATION_OWNER" not in queue:
        fail("queue configured-owner authorization is missing")
    if "github.triggering_actor" in queue:
        fail("queue must not depend on github.triggering_actor")
    if 'body.strip() == trigger' not in queue:
        fail("queue exact standalone comment trigger is missing")
    if "trusted_run_id" not in queue or "actions/runs/{run_id}" not in queue:
        fail("queue bot dispatch is not bound to a concrete supervisor run")

    reconcile = workflow("ci-reconcile.yml")
    if 'workflows: ["CI", "Unit Tests"]' not in reconcile:
        fail("reconciliation must start only from fixed public workflows or schedule")
    if "python -m scripts.supervisor_runtime discover" not in reconcile:
        fail("reconciliation does not use bounded default-branch discovery")
    if "gh workflow run trusted-checks.yml" not in reconcile:
        fail("reconciliation does not dispatch the fixed trusted workflow")
    if '--ref "$DEFAULT_BRANCH"' not in reconcile or '-f "target_sha=$TARGET_SHA"' not in reconcile:
        fail("reconciliation dispatch is not fixed to default branch and exact candidate")
    if "actions: write" not in reconcile or "max-parallel: 2" not in reconcile:
        fail("reconciliation dispatch permission or bound is missing")
    if "statuses: write" in reconcile or "/statuses/" in reconcile:
        fail("reconciliation must not publish orphan statuses")

    supervisor = workflow("supervisor.yml")
    if "\n  pull_request:\n" in supervisor:
        fail("write-capable supervisor must not load from a proposed Pull Request ref")
    if "ref: ${{ github.event.repository.default_branch }}" not in supervisor:
        fail("write-capable supervisor must checkout the default branch explicitly")

    runtime = (ROOT / "scripts/supervisor_runtime.py").read_text(encoding="utf-8")
    for required in (
        "trusted_workflow_id()",
        "current_default_sha()",
        'run.get("event") != "workflow_dispatch"',
        'run.get("display_title") != f"Trusted checks {sha}"',
        "trusted_runs_for_sha",
        "expected_external_id",
        "previous_filename",
        "exact_codex_clean",
        "merge_method=squash",
        'f"sha={sha}"',
    ):
        if required not in runtime:
            fail(f"supervisor runtime invariant is missing: {required}")
    request_function = runtime.split("def request_codex", 1)[1].split("def supervise", 1)[0]
    if 'get("login") == ACTIONS_LOGIN' not in request_function:
        fail("Codex request deduplication trusts untrusted marker comments")

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

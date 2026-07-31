#!/usr/bin/env python3
"""Validate required public foundation structure and workflow invariants."""
from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE_BOOTSTRAP_DIR = ROOT / "bootstrap"
SOURCE_GENERATOR = SOURCE_BOOTSTRAP_DIR / "generator.py"
GENERATED_TARGET_MARKER = ROOT / ".foundation-generated-target"
GENERATED_TARGET_MARKER_CONTENT = "ai-dev-automation-foundation-bootstrap-v1\n"
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
    generated_target = GENERATED_TARGET_MARKER.is_file()
    if generated_target:
        if GENERATED_TARGET_MARKER.read_text(encoding="utf-8") != GENERATED_TARGET_MARKER_CONTENT:
            fail("generated-target marker is invalid")
        if SOURCE_BOOTSTRAP_DIR.exists():
            fail("generated target must not contain the Foundation Bootstrap source directory")
        if not (ROOT / "INSTALL_CHECKLIST.md").is_file():
            fail("generated target is missing INSTALL_CHECKLIST.md")
    elif not SOURCE_GENERATOR.is_file():
        fail("Foundation source checkout is missing bootstrap/generator.py")

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
        fail("trusted checks do not verify their fixed workflow identity")
    if "WORKFLOW_SHA: ${{ github.workflow_sha }}" not in trusted:
        fail("trusted checks do not verify the current default-branch workflow SHA")
    if "name: CI / validate" not in trusted or "name: Unit Tests / test" not in trusted:
        fail("trusted exact-SHA GitHub-owned job names are missing")
    for forbidden in (
        "checks: write",
        "/check-runs",
        '"external_id"',
        "finalize:",
        "statuses: write",
    ):
        if forbidden in trusted:
            fail(f"trusted workflow still contains custom metadata publication: {forbidden}")

    authorize = job_block(trusted, "authorize", "validate_target")
    validate = job_block(trusted, "validate_target", "test_target")
    test = job_block(trusted, "test_target")

    for required in ("contents: read", "pull-requests: read"):
        if required not in authorize:
            fail(f"trusted authorize job is missing {required}")
    for forbidden in ("issues: write", "checks: write", "actions/checkout@", "secrets."):
        if forbidden in authorize:
            fail(f"trusted authorize job has forbidden capability or candidate execution: {forbidden}")
    for required in ("WORKFLOW_REF", "WORKFLOW_SHA", "pr_number="):
        if required not in authorize:
            fail(f"trusted authorize identity invariant is missing: {required}")

    for name, block, display_name in (
        ("validate", validate, "name: CI / validate"),
        ("test", test, "name: Unit Tests / test"),
    ):
        if display_name not in block:
            fail(f"trusted {name} job name is not fixed")
        if "permissions:\n      contents: read" not in block:
            fail(f"trusted {name} execution job must be read-only")
        for forbidden in ("checks: write", "id-token: write", "secrets.", "issues: write"):
            if forbidden in block:
                fail(f"trusted {name} execution job has write/secret capability")
        if "actions/checkout@" not in block or "ref: ${{ env.TARGET_SHA }}" not in block:
            fail(f"trusted {name} job is not bound to the immutable candidate")
        if "persist-credentials: false" not in block:
            fail(f"trusted {name} job persists credentials")
        if 'test "$(git rev-parse HEAD)" = "$TARGET_SHA"' not in block:
            fail(f"trusted {name} job does not verify the checked-out SHA")

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
    if '"Trusted Exact-SHA Checks"' not in supervisor:
        fail("supervisor must reconcile immediately after trusted checks complete")

    runtime = (ROOT / "scripts/supervisor_runtime.py").read_text(encoding="utf-8")
    for required in (
        "trusted_workflow_id()",
        "current_default_sha()",
        'run.get("event") != "workflow_dispatch"',
        'run.get("display_title") != f"Trusted checks {sha}"',
        "trusted_runs_for_sha",
        "trusted_run_jobs",
        'actions/runs/{run_id}/jobs?filter=all',
        "ATTESTATION_JOB_NAMES",
        "_complete_successful_job_set",
        "previous_filename",
        "exact_codex_clean",
        "MAX_ATTESTATION_ATTEMPTS",
        "merge_method=squash",
        'f"sha={sha}"',
    ):
        if required not in runtime:
            fail(f"supervisor runtime invariant is missing: {required}")
    for forbidden in ("/check-runs", "external_id", "run_id_from_details_url"):
        if forbidden in runtime:
            fail(f"supervisor still trusts custom check metadata: {forbidden}")
    request_function = runtime.split("def request_codex", 1)[1].split("def supervise", 1)[0]
    if 'get("login") == ACTIONS_LOGIN' not in request_function:
        fail("Codex request deduplication trusts untrusted marker comments")

    if not generated_target:
        generator = SOURCE_GENERATOR.read_text(encoding="utf-8")
        if '".github/workflows/trusted-checks.yml"' not in generator:
            fail("Bootstrap allowlist does not include trusted exact-SHA checks")
        if '"README.md"' not in generator or '"LICENSE"' not in generator:
            fail("Bootstrap allowlist does not include public README and license")
        if 'GENERATED_TARGET_MARKER' not in generator:
            fail("Bootstrap generator does not identify rendered targets explicitly")

    print("repository validation: clean")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

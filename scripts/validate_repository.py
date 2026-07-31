#!/usr/bin/env python3
"""Validate required public foundation structure and workflow invariants."""
from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE_MARKER = ROOT / "tests/test_bootstrap.py"
SOURCE_GENERATOR = ROOT / "bootstrap/generator.py"
GENERATED_TARGET_MARKER = "<!-- ai-dev-automation-foundation:generated-target -->"
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


def function_block(text: str, name: str, next_name: str) -> str:
    marker = f"def {name}("
    boundary = f"\ndef {next_name}("
    if marker not in text or boundary not in text.split(marker, 1)[1]:
        fail(f"runtime function boundary missing: {name} -> {next_name}")
    return text.split(marker, 1)[1].split(boundary, 1)[0]


def require_all(text: str, values: tuple[str, ...], context: str) -> None:
    for value in values:
        if value not in text:
            fail(f"{context} invariant is missing: {value}")


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

    for name in ("ci.yml", "unit-tests.yml"):
        text = workflow(name)
        require_all(
            text,
            ("\n  pull_request:\n", "permissions:\n  contents: read", "persist-credentials: false"),
            name,
        )
        for forbidden in (
            "secrets.",
            "id-token: write",
            "checks: write",
            "statuses: write",
            "actions: write",
            "contents: write",
        ):
            if forbidden in text:
                fail(f"{name}: forbidden contributor capability {forbidden}")
    require_all(
        workflow("ci.yml"),
        ("Psych.parse_stream", "documents.length == 1"),
        "CI YAML parsing",
    )

    trusted = workflow("trusted-checks.yml")
    require_all(
        trusted,
        (
            "run-name: Trusted checks ${{ inputs.target_sha }}",
            "\n  workflow_dispatch:\n",
            "WORKFLOW_REF: ${{ github.workflow_ref }}",
            "WORKFLOW_SHA: ${{ github.workflow_sha }}",
            "name: CI / validate",
            "name: Unit Tests / test",
        ),
        "trusted checks",
    )
    for forbidden in (
        "workflow_call:",
        "checks: write",
        "/check-runs",
        '"external_id"',
        "finalize:",
        "statuses: write",
    ):
        if forbidden in trusted:
            fail(f"trusted workflow has forbidden metadata path: {forbidden}")
    authorize = job_block(trusted, "authorize", "validate_target")
    require_all(
        authorize,
        ("contents: read", "pull-requests: read", "WORKFLOW_REF", "WORKFLOW_SHA", "pr_number="),
        "trusted authorize",
    )
    for forbidden in ("issues: write", "checks: write", "actions/checkout@", "secrets."):
        if forbidden in authorize:
            fail(f"trusted authorize has forbidden capability: {forbidden}")
    for block in (
        job_block(trusted, "validate_target", "test_target"),
        job_block(trusted, "test_target"),
    ):
        require_all(
            block,
            (
                "permissions:\n      contents: read",
                "actions/checkout@",
                "persist-credentials: false",
                'test "$(git rev-parse HEAD)" = "$TARGET_SHA"',
            ),
            "trusted candidate job",
        )
        for forbidden in ("checks: write", "id-token: write", "secrets.", "issues: write"):
            if forbidden in block:
                fail(f"trusted candidate job has forbidden capability: {forbidden}")

    queue = workflow("claude-queue.yml")
    require_all(
        queue,
        (
            "github.actor == github.repository_owner",
            "github.actor == vars.AUTOMATION_OWNER",
            'body.strip() == trigger',
            "trusted_run_id",
            "actions/runs/{run_id}",
        ),
        "Queue",
    )
    if "github.triggering_actor" in queue:
        fail("Queue must not depend on github.triggering_actor")

    reconcile = workflow("ci-reconcile.yml")
    require_all(
        reconcile,
        (
            'workflows: ["CI", "Unit Tests"]',
            "python -m scripts.supervisor_runtime discover",
            "gh workflow run trusted-checks.yml",
            '--ref "$DEFAULT_BRANCH"',
            '-f "target_sha=$TARGET_SHA"',
            "actions: write",
            "max-parallel: 2",
        ),
        "reconciliation",
    )
    if "statuses: write" in reconcile or "/statuses/" in reconcile:
        fail("reconciliation must not publish orphan statuses")

    supervisor = workflow("supervisor.yml")
    require_all(
        supervisor,
        (
            '"Trusted Exact-SHA Checks"',
            "ref: ${{ github.event.repository.default_branch }}",
            "persist-credentials: false",
            "contents: write",
        ),
        "supervisor workflow",
    )
    if "\n  pull_request:\n" in supervisor or "secrets." in supervisor or "id-token: write" in supervisor:
        fail("write-capable supervisor is not default-branch/fork safe")

    policy = (ROOT / "scripts/supervisor_policy.py").read_text(encoding="utf-8")
    require_all(
        policy,
        (
            "declared_paths",
            "scope_is_authorized",
            "protected_scope_is_authorized",
            'authorized.endswith("/**")',
            "fnmatchcase",
        ),
        "source scope policy",
    )

    runtime = (ROOT / "scripts/supervisor_runtime.py").read_text(encoding="utf-8")
    require_all(
        runtime,
        (
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
            "exact_codex_evidence",
            "_codex_items",
            "_codex_event_timestamp",
            "MAX_ATTESTATION_ATTEMPTS",
            "merge_method=squash",
            'f"sha={sha}"',
            'INTERNAL_STOP_BRANCH = "automation-internal-stops"',
            'INTERNAL_STOP_ROOT = "automation-stops"',
            "internal_stop_record_path",
            "human_notice_record_path",
            "canonical_internal_stop_record",
            "canonical_human_notice_record",
            "ensure_internal_stop_branch",
            "persist_internal_stop_record",
            "persist_human_notice_record",
            "latest_successful_attestation_timestamp",
            "request_timestamp",
            'f"repos/{REPO}/commits/{sha}/check-runs?per_page=100"',
            'f"repos/{REPO}/collaborators/{AUTOMATION_OWNER}/permission"',
            "AUDIT_WORKFLOWS",
            "initial_and_final_head_confirmed=true",
            '"notification": False',
            '"required_human_action": None',
            "scope_is_authorized(changed, issue_body)",
            '"UNAUTHORIZED_CHANGED_PATH"',
            "NATIVE_WORKFLOW_SPECS",
            "OPTIONAL_NATIVE_WORKFLOW_SPECS",
            "required_native_workflows",
            "native_workflow_evidence",
            '"e2e.yml", "E2E Acceptance"',
        ),
        "runtime",
    )
    for forbidden in ("external_id", "run_id_from_details_url", "/commits/{sha}/status"):
        if forbidden in runtime:
            fail(f"runtime trusts prohibited custom metadata: {forbidden}")

    stop = function_block(runtime, "stop_report", "format_human_only_notice")
    for forbidden in ("comment(", "ensure_label(", "--add-label", "/comments"):
        if forbidden in stop:
            fail(f"internal stops must not notify or mutate labels: {forbidden}")
    require_all(
        stop,
        ("persist_internal_stop_record", "self_resolution_audit", "_live_pr"),
        "internal stop",
    )

    request = function_block(runtime, "request_codex", "_evidence_anchor")
    require_all(
        request,
        ('get("login") == ACTIONS_LOGIN', 'item.get("created_at") == item.get("updated_at")'),
        "Codex request dedupe",
    )
    notice = function_block(runtime, "human_only_notice", "discover_targets")
    require_all(
        notice,
        (
            "format_human_only_notice",
            "_validated_notice_destination",
            "self_resolution_audit",
            "persist_human_notice_record",
            "_existing_internal_record",
            "ACTIONS_LOGIN",
            'item.get("created_at") == item.get("updated_at")',
            "marker",
        ),
        "human-only notice",
    )
    supervise = function_block(runtime, "supervise", "main")
    if 'pr.get("updated_at")' in supervise or 'pr["updated_at"]' in supervise:
        fail("no-progress must not use Pull Request-wide updated_at")
    require_all(
        supervise,
        (
            'minutes_since(codex.get("request_timestamp"))',
            "latest_successful_attestation_timestamp(attempts)",
            "native_workflow_evidence(sha)",
            "final_native_clean",
            'scope_error in {"UNAUTHORIZED_CHANGED_PATH", "UNAUTHORIZED_PROTECTED_PATH"}',
        ),
        "terminal evidence gate",
    )

    checklist = ROOT / "INSTALL_CHECKLIST.md"
    generated_target = checklist.is_file() and GENERATED_TARGET_MARKER in checklist.read_text(encoding="utf-8")
    source_checkout = SOURCE_MARKER.is_file()
    if generated_target:
        if SOURCE_GENERATOR.exists():
            fail("generated target unexpectedly contains bootstrap/generator.py")
    elif source_checkout:
        if not SOURCE_GENERATOR.is_file():
            fail("Foundation source checkout is missing bootstrap/generator.py")
        generator = SOURCE_GENERATOR.read_text(encoding="utf-8")
        require_all(
            generator,
            (
                '".github/workflows/trusted-checks.yml"',
                '"README.md"',
                '"LICENSE"',
                "GENERATED_TARGET_MARKER",
                "automation-internal-stops",
                "never posted as Issue or Pull Request comments",
                "immutable trusted request timestamp",
                "github-actions[bot]",
                "every changed and renamed path",
                "native pull-request workflow",
                "E2E Acceptance",
            ),
            "Bootstrap parity",
        )
    else:
        fail("repository identity is ambiguous: no source marker or generated-target marker")

    print("repository validation: clean")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

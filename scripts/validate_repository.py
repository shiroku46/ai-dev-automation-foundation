#!/usr/bin/env python3
"""Validate the public automation foundation and rendered targets."""
from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
GENERATED_TARGET_MARKER = "<!-- ai-dev-automation-foundation:generated-target -->"
SOURCE_MARKER = ROOT / "tests/test_bootstrap.py"
SOURCE_GENERATOR = ROOT / "bootstrap/generator.py"
PIN = re.compile(r"uses:\s*[^@\s]+@[0-9a-f]{40}\s*$")

REQUIRED = {
    "README.md",
    "LICENSE",
    "SECURITY.md",
    "AGENTS.md",
    "CLAUDE.md",
    "docs/OPERATING_RULES.md",
    "docs/PUBLIC_SECURITY_MODEL.md",
    "scripts/public_export_guard.py",
    "scripts/validate_repository.py",
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


def fail(message: str) -> None:
    raise AssertionError(message)


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require_all(content: str, values: tuple[str, ...], context: str) -> None:
    for value in values:
        if value not in content:
            fail(f"{context} invariant is missing: {value}")


def job_block(content: str, job: str, next_job: str | None = None) -> str:
    marker = f"\n  {job}:\n"
    if marker not in content:
        fail(f"missing job {job}")
    result = content.split(marker, 1)[1]
    if next_job:
        boundary = f"\n  {next_job}:\n"
        if boundary not in result:
            fail(f"missing following job {next_job}")
        result = result.split(boundary, 1)[0]
    return result


def function_block(content: str, name: str, next_name: str) -> str:
    marker = f"def {name}("
    boundary = f"\ndef {next_name}("
    if marker not in content:
        fail(f"missing function {name}")
    result = content.split(marker, 1)[1]
    if boundary not in result:
        fail(f"runtime function boundary missing: {name} -> {next_name}")
    return result.split(boundary, 1)[0]


def validate_workflows() -> None:
    workflows = sorted((ROOT / ".github/workflows").glob("*.yml"))
    if not workflows:
        fail("no workflows found")
    for path in workflows:
        content = path.read_text(encoding="utf-8")
        if "\t" in content:
            fail(f"{path}: tab indentation is prohibited")
        if not content.startswith("name:") or "\non:\n" not in content or "\njobs:\n" not in content:
            fail(f"{path}: required top-level sections are missing")
        if "pull_request_target" in content:
            fail(f"{path}: pull_request_target is prohibited")
        for line in content.splitlines():
            stripped = line.strip().removeprefix("- ")
            if stripped.startswith("uses:") and not PIN.search(line):
                fail(f"{path}: action is not pinned to a 40-character commit")

    for name in ("ci.yml", "unit-tests.yml"):
        content = text(f".github/workflows/{name}")
        require_all(
            content,
            ("\n  pull_request:\n", "permissions:\n  contents: read", "persist-credentials: false"),
            name,
        )
        for forbidden in (
            "secrets.",
            "id-token: write",
            "actions: write",
            "checks: write",
            "statuses: write",
            "contents: write",
        ):
            if forbidden in content:
                fail(f"{name}: contributor job has forbidden capability {forbidden}")
    require_all(text(".github/workflows/ci.yml"), ("Psych.parse_stream", "documents.length == 1"), "CI parser")

    trusted = text(".github/workflows/trusted-checks.yml")
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
    for forbidden in ("workflow_call:", "checks: write", "statuses: write", "/check-runs", '"external_id"'):
        if forbidden in trusted:
            fail(f"trusted checks contain forbidden evidence path {forbidden}")
    authorize = job_block(trusted, "authorize", "validate_target")
    for forbidden in ("actions/checkout@", "secrets.", "issues: write", "checks: write"):
        if forbidden in authorize:
            fail(f"trusted authorize job has forbidden capability {forbidden}")
    for candidate_job in (
        job_block(trusted, "validate_target", "test_target"),
        job_block(trusted, "test_target"),
    ):
        require_all(
            candidate_job,
            ("permissions:\n      contents: read", "persist-credentials: false", 'test "$(git rev-parse HEAD)" = "$TARGET_SHA"'),
            "trusted candidate job",
        )
        for forbidden in ("secrets.", "id-token: write", "contents: write", "checks: write"):
            if forbidden in candidate_job:
                fail(f"trusted candidate job has forbidden capability {forbidden}")

    queue = text(".github/workflows/claude-queue.yml")
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

    reconcile = text(".github/workflows/ci-reconcile.yml")
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
        fail("reconciliation must not publish custom statuses")

    supervisor = text(".github/workflows/supervisor.yml")
    require_all(
        supervisor,
        (
            '"Trusted Exact-SHA Checks"',
            "ref: ${{ github.event.repository.default_branch }}",
            "persist-credentials: false",
            "contents: write",
        ),
        "supervisor",
    )
    for forbidden in ("\n  pull_request:\n", "secrets.", "id-token: write"):
        if forbidden in supervisor:
            fail(f"write-capable supervisor is not fork safe: {forbidden}")


def validate_policy_and_runtime() -> None:
    policy = text("scripts/supervisor_policy.py")
    require_all(
        policy,
        (
            "declared_paths",
            "protected_authorized_paths",
            "scope_is_authorized",
            "protected_scope_is_authorized",
            'authorized.endswith("/**")',
            'any(character in authorized for character in "*?[")',
            'line.startswith("<!--")',
        ),
        "source scope policy",
    )

    runtime = text("scripts/supervisor_runtime.py")
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
            "scope_is_authorized(changed, issue_body)",
            "protected_scope_is_authorized",
            '"UNAUTHORIZED_CHANGED_PATH"',
            "_codex_items",
            "_codex_event_timestamp",
            "_workflow_definition_matches_default",
            "_content_blob_sha",
            "candidate_blob == default_blob",
            "_run_belongs_to_pr",
            "native_workflow_evidence",
            '"e2e.yml", "E2E Acceptance"',
            "_connected_repository_creation_evidence",
            "_connected_human_notice_evidence",
            "Caller attempted-path assertions do not match connected audit evidence",
            "No reason-specific connected provider evidence adapter is available",
            'INTERNAL_STOP_BRANCH = "automation-internal-stops"',
            "canonical_internal_stop_record",
            "canonical_human_notice_record",
            "persist_internal_stop_record",
            "persist_human_notice_record",
            '"notification": False',
            '"required_human_action": None',
            "merge_method=squash",
            'f"sha={sha}"',
        ),
        "runtime",
    )
    for forbidden in ("external_id", "run_id_from_details_url", "/commits/{sha}/status"):
        if forbidden in runtime:
            fail(f"runtime trusts forbidden metadata {forbidden}")

    stop = function_block(runtime, "stop_report", "format_human_only_notice")
    require_all(stop, ("self_resolution_audit", "persist_internal_stop_record", "_live_pr"), "internal stop")
    for forbidden in ("comment(", "ensure_label(", "--add-label", "/comments"):
        if forbidden in stop:
            fail(f"routine stop notifies or mutates labels: {forbidden}")

    notice = function_block(runtime, "human_only_notice", "discover_targets")
    require_all(
        notice,
        (
            "_connected_human_notice_evidence",
            "format_human_only_notice",
            "_validated_notice_destination",
            "self_resolution_audit",
            "persist_human_notice_record",
            "_existing_internal_record",
            "ACTIONS_LOGIN",
            'item.get("created_at") == item.get("updated_at")',
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
            "native_workflow_evidence(sha, pr_number)",
            "final_native_clean",
            'scope_error in {"UNAUTHORIZED_CHANGED_PATH", "UNAUTHORIZED_PROTECTED_PATH"}',
        ),
        "terminal evidence gate",
    )


def validate_identity() -> None:
    checklist = ROOT / "INSTALL_CHECKLIST.md"
    generated = checklist.is_file() and GENERATED_TARGET_MARKER in checklist.read_text(encoding="utf-8")
    source = SOURCE_MARKER.is_file()
    if generated:
        if SOURCE_GENERATOR.exists():
            fail("generated target unexpectedly contains bootstrap/generator.py")
        return
    if source:
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
                "every changed and renamed path",
                "protected-change authorization",
                "native pull-request workflow evidence",
                "E2E Acceptance",
                "automation-internal-stops",
                "never posted as Issue or Pull Request comments",
                "github-actions[bot]",
                "automatic-resumption condition",
            ),
            "Bootstrap parity",
        )
        return
    fail("repository identity is ambiguous: no source marker or generated-target marker")


def main() -> int:
    missing = sorted(path for path in REQUIRED if not (ROOT / path).is_file())
    if missing:
        fail(f"missing required files: {missing}")
    validate_workflows()
    validate_policy_and_runtime()
    validate_identity()
    print("repository validation: clean")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

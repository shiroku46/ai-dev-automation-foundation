#!/usr/bin/env python3
"""Validate the GitHub-only Foundation and byte-equivalent generated targets."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN = re.compile(r"uses:\s*[^@\s]+@[0-9a-f]{40}\s*$")
GENERATED_TARGET_MARKER = "<!-- ai-dev-automation-foundation:generated-target -->"

REQUIRED = {
    "README.md",
    "LICENSE",
    "SECURITY.md",
    "AGENTS.md",
    "CLAUDE.md",
    "docs/PROJECT_STARTUP.md",
    "docs/MINIMUM_SAFETY_PROFILE.md",
    "docs/OPERATING_RULES.md",
    "docs/PUBLIC_SECURITY_MODEL.md",
    "scripts/public_export_guard.py",
    "scripts/validate_repository.py",
    "scripts/queue_failure_classifier.py",
    "scripts/github_coordinator_supervisor.py",
    "scripts/ai_recovery_supervisor.py",
    "scripts/supervisor_final_guard.py",
    "scripts/supervisor_policy.py",
    "scripts/supervisor_runtime.py",
    "scripts/supervisor_queue_recovery.py",
    "scripts/supervisor_queue_recovery_v2.py",
    "scripts/supervisor_queue_recovery_v3.py",
    "bootstrap/generator.py",
    ".github/workflows/ci.yml",
    ".github/workflows/unit-tests.yml",
    ".github/workflows/trusted-checks.yml",
    ".github/workflows/claude-queue.yml",
    ".github/workflows/ci-reconcile.yml",
    ".github/workflows/supervisor.yml",
    ".github/ISSUE_TEMPLATE/ai-task.yml",
    ".github/pull_request_template.md",
}


class ValidationError(AssertionError):
    pass


def fail(message: str) -> None:
    raise ValidationError(message)


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
    block = content.split(marker, 1)[1]
    if next_job:
        boundary = f"\n  {next_job}:\n"
        if boundary not in block:
            fail(f"missing following job {next_job}")
        block = block.split(boundary, 1)[0]
    return block


def validate_required_files() -> None:
    missing = sorted(path for path in REQUIRED if not (ROOT / path).is_file())
    if missing:
        fail("required Foundation files are missing: " + ", ".join(missing))


def validate_action_pins() -> None:
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


def validate_contributor_checks() -> None:
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


def validate_supervisor() -> None:
    content = text(".github/workflows/supervisor.yml")
    require_all(
        content,
        (
            'workflows: ["CI", "Unit Tests"]',
            "issue_comment:",
            "schedule:",
            "workflow_dispatch:",
            "ref: ${{ github.event.repository.default_branch }}",
            "persist-credentials: false",
            "actions: read",
            "contents: write",
            "issues: read",
            "pull-requests: write",
            "python -m scripts.github_coordinator_supervisor",
        ),
        "GitHub coordinator supervisor",
    )
    for forbidden in (
        "secrets.",
        "id-token: write",
        "actions: write",
        "issues: write",
        "Claude Issue Queue",
        "supervisor_final_guard",
        "supervisor_queue_recovery",
        "codex",
        "anthropic",
    ):
        if forbidden.lower() in content.lower():
            fail(f"Supervisor contains forbidden provider or permission path: {forbidden}")


def validate_optional_queue() -> None:
    content = text(".github/workflows/claude-queue.yml")
    require_all(
        content,
        (
            'trigger = "/claude-run"',
            "body.strip() == trigger",
            "check_tool_permission_contract",
            "contract_ok",
            "continue-on-error: true",
            "track_progress: false",
            "reserve the final 5 turns",
            'checkpoint_kind = "complete"',
            'checkpoint_kind = "wip"',
            "notification: false",
            "human_action_required",
        ),
        "optional Claude Queue",
    )
    active = "\n".join(
        line for line in content.splitlines() if not line.lstrip().startswith("#")
    )
    if "track_progress: true" in active:
        fail("optional Queue must remain in agent mode")
    implement = job_block(content, "implement", "verify")
    require_all(
        implement,
        (
            "contents: read",
            "issues: read",
            "pull-requests: read",
            "id-token: write",
            "persist-credentials: false",
        ),
        "optional provider implementation job",
    )
    for forbidden in ("contents: write", "issues: write", "pull-requests: write"):
        if forbidden in implement:
            fail(f"optional provider implementation job can write: {forbidden}")
    publish = job_block(content, "publish", "finalize")
    for forbidden in ("secrets.", "id-token: write", "anthropics/", "claude_code_oauth_token"):
        if forbidden in publish:
            fail(f"write-capable Queue publication contains provider credentials: {forbidden}")


def validate_reconciliation() -> None:
    content = text(".github/workflows/ci-reconcile.yml")
    require_all(
        content,
        (
            'workflows: ["CI", "Unit Tests"]',
            "actions: read",
            "contents: read",
            "read-only compatibility observation",
            "provider_invocation: false",
            "human_action_required: false",
        ),
        "read-only CI reconciliation",
    )
    for forbidden in (
        "actions: write",
        "contents: write",
        "issues: write",
        "pull-requests: write",
        "secrets.",
        "id-token: write",
        "supervisor_queue_recovery",
        "Claude Issue Queue",
    ):
        if forbidden in content:
            fail(f"reconciliation retains a forbidden recovery capability: {forbidden}")


def validate_runtime_contract() -> None:
    supervisor = text("scripts/github_coordinator_supervisor.py")
    require_all(
        supervisor,
        (
            "foundation-coordinator-review",
            "foundation-protected-authorization-amendment",
            "ai-no-merge",
            "workflow differs from the default-branch definition",
            "review evidence changed during evaluation",
            "expected-head merge was rejected",
            "human_action_required",
        ),
        "GitHub coordinator runtime",
    )
    if "secrets." in supervisor or "actions/checkout" in supervisor.lower():
        fail("GitHub coordinator runtime must not access Secrets or check out candidate code")

    classifier = text("scripts/queue_failure_classifier.py")
    require_all(
        classifier,
        (
            "optional_provider_explicitly_enabled",
            "credential_ui_only_proven",
            "optional provider route unavailable; continue GitHub-direct work",
        ),
        "optional provider failure policy",
    )


def validate_templates_and_policy() -> None:
    issue_template = text(".github/ISSUE_TEMPLATE/ai-task.yml")
    require_all(
        issue_template,
        (
            "Risk tier",
            "Allowed paths",
            "Required checks",
            "Prohibited effects",
            "Rollback",
            "GitHub-only",
        ),
        "Issue template",
    )
    pr_template = text(".github/pull_request_template.md")
    require_all(
        pr_template,
        (
            "implementation_route",
            "github-direct",
            "review_route",
            "github-coordinator",
            "review_state",
            "unresolved_review_threads",
            "human_action_required",
            "exact_head_sha",
        ),
        "Pull Request template",
    )
    combined = "\n".join(
        text(path)
        for path in (
            "README.md",
            "AGENTS.md",
            "CLAUDE.md",
            "docs/PROJECT_STARTUP.md",
            "docs/MINIMUM_SAFETY_PROFILE.md",
            "docs/OPERATING_RULES.md",
        )
    )
    require_all(
        combined,
        ("github-direct", "github-coordinator", "expected-head", "Codex and Claude"),
        "GitHub-only policy",
    )
    for forbidden in (
        "standard and protected work require one clean exact-SHA audit",
        "Codex environment confirmed;",
        "Secret **name** confirmed, never its value;",
    ):
        if forbidden in combined:
            fail(f"mandatory provider policy remains: {forbidden}")


def validate_bootstrap_contract() -> None:
    generator = text("bootstrap/generator.py")
    require_all(
        generator,
        (
            "MANAGED_FILES",
            "write_bytes(source.read_bytes())",
            "scripts/github_coordinator_supervisor.py",
            "scripts/queue_failure_classifier.py",
            ".github/workflows/supervisor.yml",
            "Codex and Claude setup is optional",
        ),
        "Bootstrap generator",
    )
    checklist = ROOT / "INSTALL_CHECKLIST.md"
    if checklist.exists():
        content = checklist.read_text(encoding="utf-8")
        if GENERATED_TARGET_MARKER not in content:
            fail("generated target checklist marker is missing")
        require_all(
            content,
            (
                "Mandatory GitHub Phase 0",
                "GitHub coordinator review",
                "Codex and Claude setup is optional",
                "human_action_required: false",
            ),
            "generated target checklist",
        )


def main() -> int:
    try:
        validate_required_files()
        validate_action_pins()
        validate_contributor_checks()
        validate_supervisor()
        validate_optional_queue()
        validate_reconciliation()
        validate_runtime_contract()
        validate_templates_and_policy()
        validate_bootstrap_contract()
        print("repository validation passed")
        return 0
    except (OSError, ValidationError, ValueError) as exc:
        print(f"repository validation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

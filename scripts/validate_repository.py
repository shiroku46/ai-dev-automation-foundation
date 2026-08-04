#!/usr/bin/env python3
"""Validate GitHub-only runtime, optional-provider isolation and Bootstrap parity."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN = re.compile(r"uses:\s*[^@\s]+@[0-9a-f]{40}\s*$")
REQUIRED = {
    "README.md", "LICENSE", "SECURITY.md", "AGENTS.md", "CLAUDE.md",
    "docs/PROJECT_STARTUP.md", "docs/MINIMUM_SAFETY_PROFILE.md",
    "docs/OPERATING_RULES.md", "docs/PUBLIC_SECURITY_MODEL.md",
    "scripts/public_export_guard.py", "scripts/validate_repository.py",
    "scripts/queue_failure_classifier.py", "scripts/github_coordinator_supervisor.py",
    ".github/workflows/ci.yml", ".github/workflows/unit-tests.yml",
    ".github/workflows/claude-queue.yml",
    ".github/workflows/ci-reconcile.yml", ".github/workflows/supervisor.yml",
    ".github/ISSUE_TEMPLATE/ai-task.yml", ".github/pull_request_template.md",
}
FOUNDATION_ONLY = {"bootstrap/generator.py"}
GENERATED_TARGET_MARKER = "<!-- ai-dev-automation-foundation:generated-target -->"


class ValidationError(AssertionError):
    pass


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(content: str, values: tuple[str, ...], context: str) -> None:
    for value in values:
        if value not in content:
            raise ValidationError(f"{context} missing invariant: {value}")


def job(content: str, name: str, following: str | None = None) -> str:
    marker = f"\n  {name}:\n"
    if marker not in content:
        raise ValidationError(f"missing workflow job: {name}")
    block = content.split(marker, 1)[1]
    if following:
        block = block.split(f"\n  {following}:\n", 1)[0]
    return block


def validate() -> None:
    checklist = ROOT / "INSTALL_CHECKLIST.md"
    generated_target = (
        checklist.is_file()
        and GENERATED_TARGET_MARKER in checklist.read_text(encoding="utf-8")
    )
    required = REQUIRED if generated_target else REQUIRED | FOUNDATION_ONLY
    missing = sorted(path for path in required if not (ROOT / path).is_file())
    if missing:
        raise ValidationError("missing files: " + ", ".join(missing))

    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        content = path.read_text(encoding="utf-8")
        require(content, ("name:", "\non:\n", "\njobs:\n"), str(path))
        if "\t" in content or "pull_request_target" in content:
            raise ValidationError(f"unsafe workflow syntax: {path}")
        for line in content.splitlines():
            if line.strip().removeprefix("- ").startswith("uses:") and not PIN.search(line):
                raise ValidationError(f"unpinned action: {path}: {line.strip()}")

    for name in ("ci.yml", "unit-tests.yml"):
        content = text(f".github/workflows/{name}")
        require(content, ("pull_request:", "contents: read", "persist-credentials: false"), name)
        for forbidden in ("secrets.", "id-token: write", "contents: write", "actions: write", "checks: write"):
            if forbidden in content:
                raise ValidationError(f"candidate check has forbidden capability: {name}: {forbidden}")

    supervisor = text(".github/workflows/supervisor.yml")
    require(supervisor, (
        'workflows: ["CI", "Unit Tests"]', "issue_comment:", "schedule:",
        "ref: ${{ github.event.repository.default_branch }}", "persist-credentials: false",
        "actions: read", "issues: read", "pull-requests: write", "contents: write",
        "python -m scripts.github_coordinator_supervisor",
    ), "GitHub coordinator supervisor")
    for forbidden in ("secrets.", "id-token: write", "actions: write", "issues: write", "anthropic", "codex", "supervisor_queue_recovery"):
        if forbidden.lower() in supervisor.lower():
            raise ValidationError(f"Supervisor retains provider/write capability: {forbidden}")

    reconcile = text(".github/workflows/ci-reconcile.yml")
    require(reconcile, (
        'workflows: ["CI", "Unit Tests"]', "actions: read", "contents: read",
        "read-only compatibility observation", "provider_invocation: false",
        "human_action_required: false",
    ), "CI reconciliation")
    for forbidden in ("actions: write", "contents: write", "issues: write", "pull-requests: write", "secrets.", "id-token: write", "supervisor_queue_recovery"):
        if forbidden in reconcile:
            raise ValidationError(f"CI reconciliation retains recovery capability: {forbidden}")

    queue = text(".github/workflows/claude-queue.yml")
    require(queue, (
        'trigger = "/claude-run"', "body.strip() == trigger",
        "check_tool_permission_contract", "contract_ok", "continue-on-error: true",
        "track_progress: false", "reserve the final 5 turns", '"complete" if', 'else "wip"',
        "retry_identity", "notification: false", "human_action_required: false",
        "publication_route: GitHub-direct coordinator",
    ), "optional Queue")
    if "\n  issues:\n" in queue or "workflow_run:" in queue or "schedule:" in queue:
        raise ValidationError("optional Queue has an ordinary automatic trigger")
    implement = job(queue, "implement", "verify")
    require(implement, ("contents: read", "issues: read", "id-token: write", "persist-credentials: false"), "provider job")
    for forbidden in ("contents: write", "issues: write", "pull-requests: write"):
        if forbidden in implement:
            raise ValidationError(f"provider job can write: {forbidden}")
    verify = job(queue, "verify", "publish")
    for forbidden in ("secrets.", "id-token: write", "contents: write", "pull-requests: write"):
        if forbidden in verify:
            raise ValidationError(f"verification job has forbidden capability: {forbidden}")
    publish = job(queue, "publish", "finalize")
    require(publish, ("contents: read", "repository_write: false"), "artifact publication handoff")
    for forbidden in ("contents: write", "pull-requests: write", "secrets.", "id-token: write", "anthropics/"):
        if forbidden in publish:
            raise ValidationError(f"optional publication handoff can mutate: {forbidden}")

    runtime = text("scripts/github_coordinator_supervisor.py")
    require(runtime, (
        "foundation-coordinator-review", "foundation-protected-authorization",
        "ai-no-merge", "workflow differs from the default-branch definition",
        "review evidence changed during evaluation",
        "expected-head merge was rejected", "human_action_required",
    ), "coordinator runtime")
    classifier = text("scripts/queue_failure_classifier.py")
    require(classifier, (
        "optional_provider_explicitly_enabled", "credential_ui_only_proven",
        "optional provider route unavailable; continue GitHub-direct work",
    ), "provider failure policy")

    issue_template = text(".github/ISSUE_TEMPLATE/ai-task.yml")
    require(issue_template, ("GitHub-only", "Risk tier", "Allowed paths", "Required checks", "Prohibited effects", "Rollback"), "Issue template")
    pr_template = text(".github/pull_request_template.md")
    require(pr_template, ("implementation_route", "github-direct", "exact_head_sha", "review_route", "github-coordinator", "review_state", "unresolved_review_threads", "human_action_required"), "PR template")

    if not generated_target:
        generator = text("bootstrap/generator.py")
        require(generator, (
            "MANAGED_FILES", "write_bytes(source.read_bytes())",
            "scripts/github_coordinator_supervisor.py", ".github/workflows/supervisor.yml",
            "Codex and Claude setup is optional",
        ), "Bootstrap")


def main() -> int:
    try:
        validate()
        print("repository validation passed")
        return 0
    except (OSError, ValidationError, ValueError) as exc:
        print(f"repository validation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

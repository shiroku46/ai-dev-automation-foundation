#!/usr/bin/env python3
"""Render the reviewed public automation foundation into a target repository."""
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATED_TARGET_MARKER = "<!-- ai-dev-automation-foundation:generated-target -->"
ALLOWLIST = [
    "README.md",
    "LICENSE",
    "AGENTS.md",
    "CLAUDE.md",
    "SECURITY.md",
    "docs/OPERATING_RULES.md",
    "docs/PUBLIC_SECURITY_MODEL.md",
    "docs/MINIMUM_SAFETY_PROFILE.md",
    "scripts/public_export_guard.py",
    "scripts/validate_repository.py",
    "scripts/ai_recovery_supervisor.py",
    "scripts/supervisor_final_guard.py",
    "scripts/supervisor_policy.py",
    "scripts/supervisor_runtime.py",
    "scripts/supervisor_queue_recovery.py",
    "scripts/supervisor_queue_recovery_v2.py",
    "scripts/supervisor_queue_recovery_v3.py",
    ".github/workflows/ci.yml",
    ".github/workflows/unit-tests.yml",
    ".github/workflows/trusted-checks.yml",
    ".github/workflows/claude-queue.yml",
    ".github/workflows/ci-reconcile.yml",
    ".github/workflows/supervisor.yml",
    ".github/ISSUE_TEMPLATE/ai-task.yml",
    ".github/pull_request_template.md",
]


def render(target: Path, owner: str) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for relative in ALLOWLIST:
        source = ROOT / relative
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    (target / "INSTALL_CHECKLIST.md").write_text(
        f"{GENERATED_TARGET_MARKER}\n"
        "# Installation checklist\n\n"
        "## Phase 0 — complete before product work\n\n"
        "- [ ] Connect Codex/ChatGPT to GitHub and authorize this exact repository.\n"
        "- [ ] Create a Codex Environment for this exact repository.\n"
        "- [ ] On the owner's authenticated local machine, run `claude setup-token`.\n"
        "- [ ] Store the value only as repository Actions Secret `CLAUDE_CODE_OAUTH_TOKEN`; never paste it into chat, Notion, Issues, Pull Requests, source, workflows, or logs.\n"
        "- [ ] Run one harmless Bootstrap acceptance Issue before product implementation.\n\n"
        "## Repository identity\n\n"
        f"- [ ] Optionally set repository variable `AUTOMATION_OWNER` to `{owner}`; "
        "the repository owner is the fail-closed default.\n"
        "- [ ] Confirm all Foundation workflows are active on the default branch.\n"
        "- [ ] Run the public export guard, repository validator, and available tests.\n\n"
        "## Minimum safety profile\n\n"
        "- [ ] Use one owner-authored `foundation-task-scope` block with `risk`, `paths`, `operation`, `prohibited`, and required `checks`.\n"
        "- [ ] During the bounded migration, legacy Issues may still contain a `protected-change authorization` block, but new Issues do not duplicate protected paths.\n"
        "- [ ] Never push automation changes directly to the default branch.\n"
        "- [ ] Treat the GitHub-visible remote head SHA as authoritative; local-only commits are incomplete.\n"
        "- [ ] Require every changed and renamed path to match the task scope.\n"
        "- [ ] Use `risk: protected` for workflows, permissions, authentication/Secret interfaces, supervisor/security policy, settings, billing, deployment/production, or destructive operations.\n"
        "- [ ] Keep candidate code out of jobs with Secrets, OIDC, or repository write permission.\n"
        "- [ ] Require exact-head Foundation checks and configured product lint, test, type-check, and build checks.\n"
        "- [ ] Low-risk documentation/tests-only changes do not require Codex.\n"
        "- [ ] Standard-risk product changes require clean exact-SHA Codex or a trusted nonempty coordinator-review marker.\n"
        "- [ ] Protected changes require clean exact-SHA Codex requested through an owner/connector-supported route.\n"
        "- [ ] `github-actions[bot]` records only a neutral review-required state and does not actively mention `@codex`.\n"
        "- [ ] Provider setup/error replies never count as review evidence and the same failed route is not retried indefinitely.\n"
        "- [ ] A head change invalidates all prior check and review evidence.\n"
        "- [ ] Immediately before merge, perform one final live PR/head/scope/check/review/mergeability recheck.\n"
        "- [ ] Merge only with the exact expected head SHA.\n"
        "- [ ] Routine failures use one idempotent status record and do not ask the owner to press Retry, approve, mark Ready, or merge.\n"
        "- [ ] Deployment and production mutation remain separately authorized.\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--owner", required=True)
    args = parser.parse_args()
    render(Path(args.target).resolve(), args.owner)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Render the reviewed Foundation into a target repository byte-for-byte."""
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATED_TARGET_MARKER = "<!-- ai-dev-automation-foundation:generated-target -->"
MANAGED_FILES = (
    "README.md", "LICENSE", "AGENTS.md", "CLAUDE.md", "SECURITY.md",
    "docs/PROJECT_STARTUP.md", "docs/MINIMUM_SAFETY_PROFILE.md",
    "docs/OPERATING_RULES.md", "docs/PUBLIC_SECURITY_MODEL.md",
    "scripts/public_export_guard.py", "scripts/validate_repository.py",
    "scripts/queue_failure_classifier.py", "scripts/github_coordinator_supervisor.py",
    "scripts/ai_recovery_supervisor.py", "scripts/supervisor_final_guard.py",
    "scripts/supervisor_policy.py", "scripts/supervisor_runtime.py",
    "scripts/supervisor_queue_recovery.py", "scripts/supervisor_queue_recovery_v2.py",
    "scripts/supervisor_queue_recovery_v3.py",
    ".github/workflows/ci.yml", ".github/workflows/unit-tests.yml",
    ".github/workflows/trusted-checks.yml", ".github/workflows/claude-queue.yml",
    ".github/workflows/ci-reconcile.yml", ".github/workflows/supervisor.yml",
    ".github/ISSUE_TEMPLATE/ai-task.yml", ".github/pull_request_template.md",
)
# Compatibility name retained for existing Bootstrap consumers and tests.
ALLOWLIST = MANAGED_FILES


def install_checklist(owner: str) -> str:
    return f"""{GENERATED_TARGET_MARKER}
# Installation checklist

## Phase 0 — Mandatory GitHub setup

- [ ] Connect ChatGPT to GitHub and authorize this exact repository.
- [ ] Confirm GitHub Actions and Foundation workflows exist on the default branch.
- [ ] Select **Read and write permissions** under `Settings` → `Actions` → `General` → `Workflow permissions`.
- [ ] Enable **Allow GitHub Actions to create and approve pull requests**.
- [ ] Optionally set `AUTOMATION_OWNER` to `{owner}` when the repository owner is not the trusted coordinator.
- [ ] Run `python scripts/public_export_guard.py .` and `python scripts/validate_repository.py`.
- [ ] Complete one harmless branch/PR candidate with exact-head checks, GitHub coordinator review, zero unresolved threads, and expected-head merge.

Codex and Claude setup is optional. Provider environment, credential, quota, account, setup or connection is not required for GitHub-only acceptance or product development.

## Bounded Queue recovery migration

- [ ] Existing consumers must rerun the Bootstrap renderer or copy every managed file byte-for-byte before relying on recovery.
- [ ] Confirm `.github/workflows/ci-reconcile.yml`, `scripts/validate_repository.py`, Queue recovery scripts, and policy documents match the same Foundation revision.
- [ ] Confirm scheduled reconciliation can resume one authorized remote checkpoint or verified artifact, or dispatch one classifier-approved bounded retry.
- [ ] Confirm duplicate branch/PR identities and exhausted retries remain non-notifying internal states with `human_action_required: false`.

Do not copy only the reconciliation workflow. Its bounded Queue recovery contract depends on the byte-equivalent classifier, policy, validator, runtime, and optional Queue files from the same revision.

## Operating boundary

- [ ] Use one trusted owner-authored Issue with risk tier, bounded paths, checks, prohibited effects, and rollback.
- [ ] Inspect current and renamed-path collisions before implementation and merge.
- [ ] Never push automation changes directly to the default branch.
- [ ] Require exact-head `CI` and `Unit Tests`.
- [ ] Require `review_route: github-coordinator` and zero unresolved review threads.
- [ ] Protected work requires explicit authorization and clean scope/security plus correctness/race markers.
- [ ] `ai-no-merge` blocks readiness and merge.
- [ ] Merge only with expected-head-SHA protection.
- [ ] Optional provider execution starts only after an owner-authored standalone `/claude-run` or explicit owner dispatch.
- [ ] Provider failure remains non-blocking with `human_action_required: false` unless a separately proven optional credential UI condition exists.
- [ ] Persist any routine automation stop only on `automation-internal-stops`; never turn it into a routine Issue or Pull Request comment.
- [ ] Never output, persist, copy, hash, or infer Secret values.
- [ ] Never execute proposed-branch code in a job carrying Secrets, OIDC, or repository write permission.
"""


def render(target: Path, owner: str) -> None:
    target = target.resolve()
    if target == ROOT.resolve():
        raise ValueError("target must not be the Foundation source directory")
    target.mkdir(parents=True, exist_ok=True)
    for relative in MANAGED_FILES:
        source = ROOT / relative
        if not source.is_file():
            raise FileNotFoundError(f"managed Foundation file is missing: {relative}")
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
    (target / "INSTALL_CHECKLIST.md").write_text(
        install_checklist(owner), encoding="utf-8", newline="\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--owner", required=True)
    args = parser.parse_args()
    render(Path(args.target), args.owner)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Render the reviewed Foundation into a target repository byte-for-byte."""
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATED_TARGET_MARKER = "<!-- ai-dev-automation-foundation:generated-target -->"

MANAGED_FILES = (
    "README.md",
    "LICENSE",
    "AGENTS.md",
    "CLAUDE.md",
    "SECURITY.md",
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
)


def install_checklist(owner: str) -> str:
    return f"""{GENERATED_TARGET_MARKER}
# Installation checklist

## Mandatory GitHub Phase 0

- [ ] Connect ChatGPT to GitHub and authorize this exact repository.
- [ ] Confirm GitHub Actions and the Foundation workflows exist on the default branch.
- [ ] Open `Settings` → `Actions` → `General` → `Workflow permissions`.
- [ ] Select **Read and write permissions**.
- [ ] Enable **Allow GitHub Actions to create and approve pull requests**.
- [ ] Save the setting.
- [ ] Optionally set `AUTOMATION_OWNER` to `{owner}` when the repository owner is not the trusted coordinator.
- [ ] Run `python scripts/public_export_guard.py .`.
- [ ] Run `python scripts/validate_repository.py`.
- [ ] Run `python -m unittest discover -s tests` when tests are installed.
- [ ] Complete one harmless same-repository branch/PR acceptance candidate with exact-head CI, GitHub coordinator review, zero unresolved threads, and expected-head merge.

Codex and Claude setup is optional. Do not wait for a provider environment, credential, quota, account, or connection before GitHub-only acceptance or product development.

## Operating boundary

- [ ] Use one trusted owner-authored Issue with risk tier, bounded paths, required checks, prohibited effects, and rollback.
- [ ] Inspect open Pull Requests for current and renamed-path collisions before implementation and merge.
- [ ] Use GitHub-visible commits on one same-repository branch; never push automation changes directly to the default branch.
- [ ] Require `CI` and `Unit Tests` from the exact current Pull Request head.
- [ ] Require `review_route: github-coordinator` and zero unresolved review threads.
- [ ] Standard work requires one exact-head clean coordinator marker.
- [ ] Protected work requires explicit protected authorization plus clean scope/security and correctness/race markers on the unchanged exact head.
- [ ] `ai-no-merge` always blocks readiness and merge.
- [ ] Final merge uses expected-head-SHA protection.
- [ ] Optional provider execution starts only after an owner-authored standalone `/claude-run` or an explicit owner workflow dispatch.
- [ ] Provider absence, quota, setup, account, connection, generic output, or stale output remains non-blocking with `human_action_required: false`.
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

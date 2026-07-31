#!/usr/bin/env python3
"""Render the reviewed public automation foundation into a target repository."""
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATED_TARGET_MARKER = ".foundation-generated-target"
GENERATED_TARGET_MARKER_CONTENT = "ai-dev-automation-foundation-bootstrap-v1\n"
ALLOWLIST = [
    "README.md",
    "LICENSE",
    "AGENTS.md",
    "CLAUDE.md",
    "SECURITY.md",
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
    (target / GENERATED_TARGET_MARKER).write_text(
        GENERATED_TARGET_MARKER_CONTENT,
        encoding="utf-8",
    )
    (target / "INSTALL_CHECKLIST.md").write_text(
        "# Installation checklist\n\n"
        f"- [ ] Optionally set repository variable `AUTOMATION_OWNER` to `{owner}`; "
        "the repository owner is the fail-closed default.\n"
        "- [ ] Review protected-change authorization.\n"
        "- [ ] Confirm the fixed default-branch `trusted-checks.yml` workflow is present.\n"
        "- [ ] Confirm candidate jobs are read-only and publish no custom checks or statuses.\n"
        "- [ ] Confirm the supervisor validates immutable workflow-run and exact job evidence.\n"
        "- [ ] Configure `CLAUDE_CODE_OAUTH_TOKEN` only through GitHub/provider UI.\n"
        "- [ ] Run export guard, validator, and tests.\n"
        "- [ ] Validate in a disposable E2E repository.\n",
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

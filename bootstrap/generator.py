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
    (target / "INSTALL_CHECKLIST.md").write_text(
        f"{GENERATED_TARGET_MARKER}\n"
        "# Installation checklist\n\n"
        f"- [ ] Optionally set repository variable `AUTOMATION_OWNER` to `{owner}`; "
        "the repository owner is the fail-closed default.\n"
        "- [ ] Require the trusted source Issue to allowlist every changed and renamed path; bounded patterns such as `tests/**` may be used.\n"
        "- [ ] Require every protected path to appear in the protected-change authorization block in addition to the ordinary Issue allowlist.\n"
        "- [ ] Confirm the fixed default-branch `trusted-checks.yml` workflow is present.\n"
        "- [ ] Confirm candidate jobs are read-only and publish no custom checks or statuses.\n"
        "- [ ] Confirm the supervisor validates immutable workflow-run and exact job evidence.\n"
        "- [ ] Confirm readiness and merge require successful exact-head native pull-request workflow evidence for `CI`, `Unit Tests`, and `E2E Acceptance` when fixed `e2e.yml` is installed.\n"
        "- [ ] Confirm missing, pending, failed, stale-SHA, wrong-workflow, wrong-repository, and candidate-authored evidence fail closed.\n"
        "- [ ] Confirm the supervisor has only the bounded `contents: write` needed for the fixed `automation-internal-stops` branch.\n"
        "- [ ] Confirm internal stops are sanitized canonical JSON at `automation-stops/pr-<number>/<sha>/<REASON>.json` and are never posted as Issue or Pull Request comments or represented by routine label mutations.\n"
        "- [ ] Confirm a failed audit or moved head writes no internal-stop record or close action.\n"
        "- [ ] Confirm Codex no-progress uses the immutable trusted request timestamp and merge-state no-progress uses the latest immutable clean evidence.\n"
        "- [ ] Confirm combined Codex comments and reviews are ordered by immutable event time before the latest exact-SHA evidence is selected.\n"
        "- [ ] Confirm only the three canonical account/provider UI reason codes can create a human-only notice.\n"
        "- [ ] Confirm every human-only notice first persists an exact deterministic audit record binding Issue, Pull Request, SHA, attempted connected paths, impossibility evidence, canonical UI action, target/provider, and automatic-resumption condition.\n"
        "- [ ] Confirm human-only deduplication requires both the exact persisted record and an immutable `github-actions[bot]` comment.\n"
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

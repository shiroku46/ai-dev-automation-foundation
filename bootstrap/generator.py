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
    "docs/PROJECT_STARTUP.md",
    "docs/OPERATING_RULES.md",
    "docs/PUBLIC_SECURITY_MODEL.md",
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
        "## Phase 0 — mandatory before acceptance or product work\n\n"
        "- [ ] Read `docs/PROJECT_STARTUP.md` for this exact repository.\n"
        "- [ ] Connect ChatGPT/Codex to GitHub and authorize this exact repository.\n"
        "- [ ] Create and verify a Codex environment for this exact repository.\n"
        "- [ ] When Claude OAuth is used, run `claude setup-token` locally and store the value only as the GitHub Actions Secret `CLAUDE_CODE_OAUTH_TOKEN`; never paste, print, log, or commit the value.\n"
        "- [ ] Confirm GitHub Actions are enabled and the Foundation workflows exist on the default branch.\n"
        "- [ ] In this exact repository, open `Settings` → `Actions` → `General` → `Workflow permissions`.\n"
        "- [ ] Select **Read and write permissions**.\n"
        "- [ ] Enable **Allow GitHub Actions to create and approve pull requests**.\n"
        "- [ ] Save the Workflow permissions setting.\n"
        "- [ ] Confirm automation can create/update a bounded branch and Pull Request, post required comments/labels, update readiness/review state, and complete the expected-head merge path.\n"
        "- [ ] Complete one harmless Bootstrap acceptance candidate and record only non-secret evidence: repository, acceptance date, Secret name, both Workflow-permissions settings, Issue/PR, exact head SHA, and successful checks/review.\n"
        "- [ ] Do not create or trigger the first product Issue until every Phase 0 item passes. Do not request these steps again after acceptance unless connected evidence shows the integration or Workflow permissions are no longer usable.\n\n"
        "## Foundation safety and merge checks\n\n"
        f"- [ ] Optionally set repository variable `AUTOMATION_OWNER` to `{owner}`; "
        "the repository owner is the fail-closed default.\n"
        "- [ ] Require the trusted source Issue to allowlist every changed and renamed path; bounded patterns such as `tests/**` may be used.\n"
        "- [ ] Keep the ordinary Issue allowlist independent from the protected-change authorization block; every protected path must appear in both.\n"
        "- [ ] Confirm the fixed default-branch `trusted-checks.yml` workflow is present.\n"
        "- [ ] Confirm candidate jobs are read-only and publish no custom checks or statuses.\n"
        "- [ ] Confirm the supervisor validates immutable workflow-run and exact job evidence.\n"
        "- [ ] Confirm readiness and merge require successful exact-head native pull-request workflow evidence for `CI`, `Unit Tests`, and `E2E Acceptance` when fixed `e2e.yml` is installed.\n"
        "- [ ] Confirm all required native workflow definitions are compared against one stable default-branch commit, and that commit is rechecked after every blob and run query.\n"
        "- [ ] Confirm each candidate workflow file blob exactly equals the blob from that one stable default-branch commit before native run evidence is trusted.\n"
        "- [ ] Confirm native runs belong to the exact Pull Request and reject missing, pending, failed, stale-SHA, wrong-workflow, wrong-repository, cross-PR, candidate-modified-workflow, and candidate-authored evidence.\n"
        "- [ ] Confirm Queue failure creates no routine Issue or Pull Request comment and no failure-state blocked/review label mutation.\n"
        "- [ ] Confirm Queue recovery is bounded, deterministic, idempotent, non-notifying, and persists only public-safe records on the fixed internal-stop branch.\n"
        "- [ ] Confirm the supervisor reconciles `Claude Issue Queue` completion through `supervisor_queue_recovery_v3` before the final merge guard.\n"
        "- [ ] Confirm trusted attestation, native workflow evidence, current source/scope authorization, candidate identity, and merge use one unchanged default-branch SHA.\n"
        "- [ ] Confirm final merge re-fetches an open, explicitly non-draft, mergeable exact-head Pull Request with explicit label evidence, no `ai-no-merge`, same-repository provenance, and the same authorized source Issue/scope.\n"
        "- [ ] Confirm the final merge evidence gate is single-use and consumed by the first merge attempt, including a rejected attempt.\n"
        "- [ ] Confirm the supervisor has only the bounded `contents: write` needed for the fixed `automation-internal-stops` branch.\n"
        "- [ ] Confirm internal stops are sanitized canonical JSON at `automation-stops/pr-<number>/<sha>/<REASON>.json` and are never posted as Issue or Pull Request comments or represented by routine label mutations.\n"
        "- [ ] Confirm a failed audit or moved head writes no internal-stop record or close action.\n"
        "- [ ] Confirm Codex no-progress uses the immutable trusted request timestamp and merge-state no-progress uses the latest immutable clean evidence.\n"
        "- [ ] Confirm combined Codex comments and reviews are ordered by immutable event time before the latest exact-SHA evidence is selected.\n"
        "- [ ] Confirm only the three canonical account/provider UI reason codes can create a human-only notice.\n"
        "- [ ] Confirm account-level repository absence is independently derived from connected GitHub API queries for the exact targets and caller assertions must match that evidence.\n"
        "- [ ] Confirm credential and integration-reconnection notices fail closed until a reason-specific connected provider evidence adapter exists.\n"
        "- [ ] Confirm every human-only notice re-derives the connected condition inside the final audit, persists an exact deterministic audit record, and rechecks the condition immediately before publication.\n"
        "- [ ] Confirm the notice record binds Issue, Pull Request, SHA, attempted connected paths, impossibility evidence, canonical UI action, target/provider, and automatic-resumption condition.\n"
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

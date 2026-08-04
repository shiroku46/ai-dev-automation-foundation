"""Security regressions for the explicitly optional Claude Queue."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT = (ROOT / ".github/workflows/claude-queue.yml").read_text(encoding="utf-8")
PIN = re.compile(r"uses:\s*[^@\s]+@[0-9a-f]{40}\s*$")


def block(name: str, following: str | None = None) -> str:
    value = TEXT.split(f"\n  {name}:\n", 1)[1]
    if following:
        value = value.split(f"\n  {following}:\n", 1)[0]
    return value


class OptionalQueueTest(unittest.TestCase):
    def test_only_explicit_events_select_provider(self):
        self.assertIn("issue_comment:\n    types: [created]", TEXT)
        self.assertIn("workflow_dispatch:", TEXT)
        self.assertNotIn("\n  issues:\n", TEXT)
        self.assertNotIn("workflow_run:", TEXT)
        self.assertNotIn("schedule:", TEXT)
        self.assertIn('trigger = "/claude-run"', TEXT)
        self.assertIn("body.strip() == trigger", TEXT)

    def test_actions_are_pinned(self):
        for line in TEXT.splitlines():
            if line.strip().removeprefix("- ").startswith("uses:"):
                self.assertRegex(line, PIN)

    def test_permission_preflight_is_before_provider(self):
        prepare = block("prepare", "implement")
        self.assertIn("check_tool_permission_contract", prepare)
        self.assertIn("foundation-provider-required-commands", prepare)
        self.assertIn("contract_ok", prepare)
        self.assertIn("model_invocation: `skipped`", prepare)
        self.assertIn("notification: false", prepare)
        self.assertIn("human_action_required: false", prepare)

    def test_provider_job_is_read_only_and_agent_mode(self):
        implement = block("implement", "verify")
        for required in (
            "contents: read", "issues: read", "pull-requests: read",
            "id-token: write", "persist-credentials: false", "track_progress: false",
            "reserve the final 5 turns", '--allowedTools "Read,Write,Edit,Glob,Grep"',
        ):
            self.assertIn(required, implement)
        for forbidden in ("contents: write", "issues: write", "pull-requests: write", "track_progress: true"):
            self.assertNotIn(forbidden, implement)

    def test_complete_or_wip_checkpoint_is_bounded(self):
        implement = block("implement", "verify")
        for required in (
            "continue-on-error: true", '"complete" if', 'else "wip"',
            "retry_identity", "changed_paths", "patch_sha256",
            "empty or unauthorized checkpoint", "checkpoint leaves must be regular files",
            "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
            "retention-days: 1",
        ):
            self.assertIn(required, implement)

    def test_checkpoint_patch_includes_authorized_untracked_files(self):
        implement = block("implement", "verify")
        self.assertIn('["git", "add", "--all", "--", *paths]', implement)
        self.assertIn('["git", "diff", "--cached", "--binary", "--no-renames", base, "--"]', implement)
        self.assertIn("empty staged checkpoint", implement)
        self.assertLess(
            implement.index('["git", "add", "--all", "--", *paths]'),
            implement.index('["git", "diff", "--cached", "--binary", "--no-renames", base, "--"]'),
        )

    def test_verification_has_no_secret_oidc_or_write(self):
        verify = block("verify", "publish")
        for required in (
            "contents: read", "persist-credentials: false", "git apply --index",
            "python scripts/public_export_guard.py .", "python scripts/validate_repository.py",
        ):
            self.assertIn(required, verify)
        for forbidden in ("secrets.", "id-token: write", "contents: write", "pull-requests: write"):
            self.assertNotIn(forbidden, verify)

    def test_handoff_has_no_repository_write(self):
        publish = block("publish", "finalize")
        self.assertIn("contents: read", publish)
        self.assertIn("publication_route: GitHub-direct coordinator", publish)
        self.assertIn("repository_write: false", publish)
        for forbidden in ("contents: write", "pull-requests: write", "secrets.", "id-token: write", "anthropics/"):
            self.assertNotIn(forbidden, publish)

    def test_final_state_is_non_notifying(self):
        finalize = block("finalize")
        self.assertIn("notification: false", finalize)
        self.assertIn("human_action_required: false", finalize)
        self.assertIn("Continue GitHub-direct work", finalize)
        self.assertNotIn("gh issue comment", finalize)


if __name__ == "__main__": unittest.main()

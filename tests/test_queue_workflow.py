"""Security regressions for the explicitly optional Claude Queue."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / ".github/workflows/claude-queue.yml"
PIN = re.compile(r"uses:\s*[^@\s]+@[0-9a-f]{40}\s*$")


def job_block(text: str, job: str, next_job: str | None = None) -> str:
    block = text.split(f"\n  {job}:\n", 1)[1]
    if next_job:
        block = block.split(f"\n  {next_job}:\n", 1)[0]
    return block


class OptionalQueueWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = QUEUE.read_text(encoding="utf-8")

    def test_only_explicit_events_select_the_provider(self):
        self.assertIn("issue_comment:\n    types: [created]", self.text)
        self.assertIn("workflow_dispatch:", self.text)
        self.assertNotIn("\n  issues:\n", self.text)
        self.assertNotIn("schedule:", self.text)
        self.assertNotIn("workflow_run:", self.text)
        self.assertIn('trigger = "/claude-run"', self.text)
        self.assertIn("body.strip() == trigger", self.text)

    def test_actions_are_pinned(self):
        for line in self.text.splitlines():
            if line.strip().removeprefix("- ").startswith("uses:"):
                self.assertRegex(line, PIN)

    def test_permission_contract_preflight_skips_contradictions(self):
        prepare = job_block(self.text, "prepare", "implement")
        self.assertIn("check_tool_permission_contract", prepare)
        self.assertIn("foundation-provider-required-commands", prepare)
        self.assertIn("contract_ok", prepare)
        self.assertIn("model_invocation: `skipped`", prepare)
        self.assertIn("notification: false", prepare)
        self.assertIn("human_action_required: false", prepare)

    def test_provider_job_is_read_only_and_agent_mode(self):
        implement = job_block(self.text, "implement", "verify")
        self.assertIn("contents: read", implement)
        self.assertIn("issues: read", implement)
        self.assertIn("pull-requests: read", implement)
        self.assertIn("id-token: write", implement)
        self.assertIn("persist-credentials: false", implement)
        self.assertNotIn("contents: write", implement)
        self.assertNotIn("issues: write", implement)
        self.assertNotIn("pull-requests: write", implement)
        active = "\n".join(
            line for line in implement.splitlines() if not line.lstrip().startswith("#")
        )
        self.assertIn("track_progress: false", active)
        self.assertNotIn("track_progress: true", active)
        self.assertNotIn("${{ github.event_name != 'workflow_dispatch' }}", active)
        self.assertIn("reserve the final 5 turns", implement)
        self.assertIn('--allowedTools "Read,Write,Edit,Glob,Grep"', implement)

    def test_nonzero_provider_outcome_persists_bounded_wip_artifact(self):
        implement = job_block(self.text, "implement", "verify")
        self.assertIn("continue-on-error: true", implement)
        self.assertIn('kind = "complete"', implement)
        self.assertIn('else "wip"', implement)
        self.assertIn("retry_identity", implement)
        self.assertIn("changed_paths", implement)
        self.assertIn("content_base64", implement)
        self.assertIn("candidate contains unauthorized", implement.replace("empty or unauthorized checkpoint", "candidate contains unauthorized"))
        self.assertIn("actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02", implement)
        self.assertIn("retention-days: 1", implement)

    def test_verify_executes_candidate_only_without_secret_or_write(self):
        verify = job_block(self.text, "verify", "publish")
        self.assertIn("permissions:\n      contents: read", verify)
        self.assertIn("persist-credentials: false", verify)
        self.assertIn("python scripts/public_export_guard.py .", verify)
        self.assertIn("python scripts/validate_repository.py", verify)
        for forbidden in ("secrets.", "id-token: write", "contents: write", "pull-requests: write"):
            self.assertNotIn(forbidden, verify)

    def test_publisher_has_no_provider_secret_or_candidate_execution(self):
        publish = job_block(self.text, "publish", "finalize")
        self.assertIn("contents: write", publish)
        self.assertIn("pull-requests: write", publish)
        self.assertIn("Git Data API", publish)
        self.assertIn("draft", publish.lower())
        for forbidden in (
            "secrets.", "id-token: write", "anthropics/", "claude_code_oauth_token",
            "python scripts/validate_repository.py", "python -m unittest",
        ):
            self.assertNotIn(forbidden, publish)

    def test_final_state_is_non_notifying_and_non_blocking(self):
        finalize = job_block(self.text, "finalize")
        self.assertIn("notification: false", finalize)
        self.assertIn("human_action_required: false", finalize)
        self.assertIn("Continue GitHub-direct work", finalize)
        self.assertNotIn("gh issue comment", finalize)
        self.assertNotIn("ai-blocked", finalize)


if __name__ == "__main__":
    unittest.main()

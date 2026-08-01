import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / ".github/workflows/claude-queue.yml"
PIN = re.compile(r"uses:\s*[^@\s]+@[0-9a-f]{40}\s*$")
HEREDOC = re.compile(r"(?ms)^\s*cat\s+>.*?<<EOF\s*$.*?^\s*EOF\s*$")


def job_block(text: str, job: str, next_job: str | None = None) -> str:
    block = text.split(f"\n  {job}:\n", 1)[1]
    if next_job:
        block = block.split(f"\n  {next_job}:\n", 1)[0]
    return block


def executable_shell(block: str) -> str:
    """Remove literal heredoc payloads before checking executable commands.

    Publication embeds the names of checks that already ran in the generated PR
    body. Those evidence strings are data, not shell execution in the write-capable
    job, so security assertions must inspect only executable shell outside heredocs.
    """

    return HEREDOC.sub("", block)


class QueueWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.text = QUEUE.read_text(encoding="utf-8")

    def test_owner_and_trusted_supervisor_admission_remain_fail_closed(self):
        self.assertIn("github.actor == github.repository_owner", self.text)
        self.assertIn("github.actor == vars.AUTOMATION_OWNER", self.text)
        self.assertIn("github.actor == 'github-actions[bot]'", self.text)
        self.assertIn("trusted_run_id:", self.text)
        self.assertIn('expected_path = f".github/workflows/ci-reconcile.yml@{default_branch}"', self.text)
        self.assertIn("snapshot.get(\"head_sha\") == default_sha", self.text)
        self.assertIn('body.strip() == trigger', self.text)
        self.assertIn("issue_author in trusted_authors", self.text)
        self.assertNotIn("github.triggering_actor", self.text)

    def test_duplicate_open_queue_pull_request_is_skipped(self):
        prepare = job_block(self.text, "prepare", "implement")
        self.assertIn("duplicate_skipped", prepare)
        self.assertIn('prefix = f"claude-issue-{issue_number}-"', prepare)
        self.assertIn("pulls?state=open&per_page=100", prepare)
        self.assertIn("foundation-queue-duplicate", prepare)

    def test_candidate_execution_is_confined_to_read_only_verify_job(self):
        verify = job_block(self.text, "verify", "publish")
        self.assertIn("permissions:\n      contents: read", verify)
        self.assertIn("persist-credentials: false", verify)
        self.assertIn('test "$(git rev-parse HEAD)" = "$TARGET_SHA"', verify)
        self.assertIn("python scripts/public_export_guard.py .", verify)
        self.assertIn("python scripts/validate_repository.py", verify)
        self.assertIn("python -m unittest discover -s tests", verify)
        self.assertNotIn("contents: write", verify)
        self.assertNotIn("issues: write", verify)
        self.assertNotIn("pull-requests: write", verify)
        self.assertNotIn("id-token: write", verify)
        self.assertNotIn("secrets.", verify)

    def test_publication_and_finalization_do_not_checkout_or_execute_candidate(self):
        publish = job_block(self.text, "publish", "finalize")
        finalize = job_block(self.text, "finalize")
        for block in (publish, finalize):
            executable = executable_shell(block)
            self.assertNotIn("actions/checkout", executable)
            self.assertNotIn("python scripts/", executable)
            self.assertNotIn("python -m unittest", executable)
            self.assertNotIn("secrets.", executable)
            self.assertNotIn("id-token: write", executable)
        # The immutable verification commands may be quoted only as evidence in
        # the generated PR body; they must not be executed by publication.
        self.assertIn("python scripts/public_export_guard.py .", publish)
        self.assertIn("python scripts/validate_repository.py", publish)
        self.assertIn("python -m unittest discover -s tests", publish)
        self.assertIn('test "$current_sha" = "$TARGET_SHA"', publish)
        self.assertIn("gh pr create", publish)
        self.assertIn("--draft", publish)
        self.assertIn("Closes #$ISSUE_NUMBER", publish)
        self.assertIn("headRefOid", publish)
        self.assertIn("notification: false", finalize)
        self.assertIn("GITHUB_STEP_SUMMARY", finalize)
        self.assertIn("default-branch reconciliation", finalize)
        self.assertNotIn("foundation-queue-stop", finalize)
        self.assertNotIn("QUEUE_PIPELINE_FAILED", finalize)
        self.assertNotIn("gh issue comment", finalize)
        self.assertNotIn("--add-label ai-blocked", finalize)
        self.assertNotIn("gh label create ai-blocked", finalize)

    def test_generated_branch_is_resolved_once_and_all_actions_are_pinned(self):
        implement = job_block(self.text, "implement", "resolve")
        resolve = job_block(self.text, "resolve", "verify")
        self.assertIn("branch_name", implement)
        self.assertIn("claude-issue-${{ needs.prepare.outputs.issue_number }}-", implement)
        self.assertIn("commits/$BRANCH", resolve)
        self.assertIn("branch_sha", resolve)
        for line in self.text.splitlines():
            if line.strip().removeprefix("- ").startswith("uses:"):
                self.assertRegex(line, PIN)


if __name__ == "__main__":
    unittest.main()

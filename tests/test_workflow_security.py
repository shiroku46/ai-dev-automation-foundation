import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WorkflowSecurityTest(unittest.TestCase):
    def test_all_workflows_have_required_sections_and_no_unsafe_trigger(self):
        workflows = sorted((ROOT / ".github/workflows").glob("*.yml"))
        self.assertTrue(workflows)
        for path in workflows:
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("name:"), path)
            self.assertIn("\non:\n", text, path)
            self.assertIn("\njobs:\n", text, path)
            self.assertNotIn("\t", text, path)
            self.assertNotIn("pull_request_target", text, path)

    def test_ci_parses_complete_single_document_yaml_streams(self):
        text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn('require "psych"', text)
        self.assertIn("Psych.parse_stream", text)
        self.assertIn("documents.length == 1", text)
        self.assertNotIn("YAML.safe_load", text)
        self.assertNotIn("pip install", text)

    def test_fork_jobs_are_read_only(self):
        for name in ("ci.yml", "unit-tests.yml"):
            text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
            self.assertIn("permissions:\n  contents: read", text)
            self.assertNotIn("secrets.", text)
            self.assertNotIn("id-token: write", text)

    def test_queue_owner_guard_correction(self):
        text = (ROOT / ".github/workflows/claude-queue.yml").read_text(encoding="utf-8")
        self.assertIn("github.actor == vars.AUTOMATION_OWNER", text)
        self.assertNotIn("github.triggering_actor", text)
        self.assertIn('body.strip() == trigger', text)

    def test_write_supervisor_uses_default_branch(self):
        text = (ROOT / ".github/workflows/supervisor.yml").read_text(encoding="utf-8")
        self.assertIn("ref: ${{ github.event.repository.default_branch }}", text)
        self.assertNotIn("github.event.pull_request.head.sha", text)


if __name__ == "__main__":
    unittest.main()

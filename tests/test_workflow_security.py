import unittest
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]

class WorkflowSecurityTest(unittest.TestCase):
    def test_all_workflows_parse_and_no_pull_request_target(self):
        for path in (ROOT / ".github/workflows").glob("*.yml"):
            text = path.read_text()
            yaml.safe_load(text)
            self.assertNotIn("pull_request_target", text)

    def test_fork_jobs_are_read_only(self):
        for name in ("ci.yml", "unit-tests.yml"):
            text = (ROOT / ".github/workflows" / name).read_text()
            self.assertIn("permissions:\n  contents: read", text)
            self.assertNotIn("secrets.", text)
            self.assertNotIn("id-token: write", text)

    def test_queue_owner_guard_correction(self):
        text = (ROOT / ".github/workflows/claude-queue.yml").read_text()
        self.assertIn("github.actor == vars.AUTOMATION_OWNER", text)
        self.assertNotIn("github.triggering_actor", text)
        self.assertIn('body.strip() == trigger', text)

    def test_write_supervisor_uses_default_branch(self):
        text = (ROOT / ".github/workflows/supervisor.yml").read_text()
        self.assertIn("ref: ${{ github.event.repository.default_branch }}", text)
        self.assertNotIn("github.event.pull_request.head.sha", text)

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
        self.assertIn("github.actor == github.repository_owner", text)
        self.assertIn("github.actor == vars.AUTOMATION_OWNER", text)
        self.assertNotIn("github.triggering_actor", text)
        self.assertIn('body.strip() == trigger', text)
        self.assertIn("github.ref_name == github.event.repository.default_branch", text)

    def test_reconciliation_uses_check_runs_and_fixed_trusted_authors(self):
        text = (ROOT / ".github/workflows/ci-reconcile.yml").read_text(encoding="utf-8")
        self.assertIn("checks: read", text)
        self.assertIn("commits/{sha}/check-runs", text)
        self.assertIn('allowed_authors = {owner, "github-actions[bot]"}', text)
        self.assertIn('{"validate": "ci.yml", "test": "unit-tests.yml"}', text)
        self.assertNotIn("statuses: write", text)
        self.assertNotIn("/statuses/", text)

    def test_write_supervisor_uses_default_branch_and_trusted_evidence(self):
        workflow = (ROOT / ".github/workflows/supervisor.yml").read_text(encoding="utf-8")
        runtime = (ROOT / "scripts/supervisor_runtime.py").read_text(encoding="utf-8")
        self.assertIn("ref: ${{ github.event.repository.default_branch }}", workflow)
        self.assertIn("checks: read", workflow)
        self.assertIn("AUTOMATION_OWNER", workflow)
        self.assertNotIn("github.event.pull_request.head.sha", workflow)
        self.assertIn("commits/{sha}/check-runs", runtime)
        self.assertIn('"github-actions[bot]"', runtime)
        self.assertIn('get("slug") != "github-actions"', runtime)
        self.assertIn("issues/comments/{request['id']}/reactions", runtime)
        self.assertIn("reviewThreads(first:100)", runtime)
        self.assertNotIn("/commits/{sha}/status", runtime)


if __name__ == "__main__":
    unittest.main()

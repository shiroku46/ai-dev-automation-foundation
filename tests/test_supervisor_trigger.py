import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR = ROOT / ".github" / "workflows" / "supervisor.yml"
EXPECTED = '    workflows: ["CI", "Unit Tests", "Trusted CI Reconciliation", "Trusted Exact-SHA Checks"]\n'


class SupervisorTriggerTest(unittest.TestCase):
    def test_exact_post_attestation_trigger_is_installed(self) -> None:
        text = SUPERVISOR.read_text(encoding="utf-8")
        workflow_run = text.split("  workflow_run:\n", 1)[1].split("  schedule:\n", 1)[0]
        self.assertEqual(
            workflow_run,
            EXPECTED + "    types: [completed]\n",
        )

    def test_write_capable_supervisor_remains_default_branch_controlled(self) -> None:
        text = SUPERVISOR.read_text(encoding="utf-8")
        self.assertNotIn("\n  pull_request:\n", text)
        self.assertIn("ref: ${{ github.event.repository.default_branch }}", text)
        self.assertIn("persist-credentials: false", text)
        self.assertNotIn("id-token: write", text)
        self.assertNotIn("secrets.", text)


if __name__ == "__main__":
    unittest.main()

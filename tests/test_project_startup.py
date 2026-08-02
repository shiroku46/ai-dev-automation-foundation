import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProjectStartupTest(unittest.TestCase):
    def test_authoritative_startup_requires_workflow_permissions(self):
        startup = (ROOT / "docs/PROJECT_STARTUP.md").read_text(encoding="utf-8")
        self.assertIn(
            "`Settings` → `Actions` → `General` → `Workflow permissions`",
            startup,
        )
        self.assertIn("Read and write permissions", startup)
        self.assertIn(
            "Allow GitHub Actions to create and approve pull requests",
            startup,
        )
        self.assertIn("save both required options", startup.lower())
        self.assertIn("before the first product Issue", startup)
        self.assertIn("Do not request these steps again", startup)

    def test_phase_zero_precedes_ordinary_flow(self):
        operating = (ROOT / "docs/OPERATING_RULES.md").read_text(encoding="utf-8")
        self.assertLess(
            operating.index("## Mandatory Phase 0"),
            operating.index("## Ordinary flow"),
        )
        self.assertIn("Read and write permissions", operating)
        self.assertIn(
            "Allow GitHub Actions to create and approve pull requests",
            operating,
        )

    def test_agents_must_present_the_same_ui_action_once(self):
        for path in ("AGENTS.md", "CLAUDE.md"):
            with self.subTest(path=path):
                content = (ROOT / path).read_text(encoding="utf-8")
                self.assertIn("docs/PROJECT_STARTUP.md", content)
                self.assertIn("Workflow permissions", content)
                self.assertIn("Read and write permissions", content)
                self.assertIn(
                    "Allow GitHub Actions to create and approve pull requests",
                    content,
                )
                self.assertIn("once", content.lower())

    def test_issue_template_records_non_secret_acceptance(self):
        template = (ROOT / ".github/ISSUE_TEMPLATE/ai-task.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("Repository startup acceptance", template)
        self.assertIn("Non-secret Phase 0 evidence reference", template)
        self.assertIn("Read and write permissions", template)
        self.assertIn(
            "Allow GitHub Actions to create and approve pull requests",
            template,
        )
        self.assertIn("Do not provide credential values", template)


if __name__ == "__main__":
    unittest.main()

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
        self.assertIn("Do not request these steps again", startup)

    def test_acceptance_is_final_phase_zero_gate_without_a_cycle(self):
        startup = (ROOT / "docs/PROJECT_STARTUP.md").read_text(encoding="utf-8")
        self.assertIn("complete setup steps 1–5 before the harmless Bootstrap acceptance", startup)
        self.assertIn("acceptance exercise in step 6 is the final Phase 0 gate", startup)
        self.assertIn("Successful acceptance completes Phase 0 and unlocks product work", startup)
        self.assertNotIn(
            "complete this repository-specific Phase 0 before the first product Issue, `/claude-run`, implementation request, or harmless Bootstrap acceptance exercise",
            startup,
        )

    def test_pre_pr_guidance_is_not_a_runtime_notice(self):
        startup = (ROOT / "docs/PROJECT_STARTUP.md").read_text(encoding="utf-8")
        self.assertIn("narrowly scoped exception to the runtime GitHub human-notice mechanism", startup)
        self.assertIn("delivered directly in the project-start conversation", startup)
        self.assertIn("does not call `human_only_notice()`", startup)
        self.assertIn("does not publish an automated GitHub notice", startup)
        self.assertIn("does not create a new runtime reason code", startup)
        self.assertIn("does not require an Issue/PR destination", startup)

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
        self.assertIn("Successful acceptance is the final Phase 0 gate", operating)
        self.assertIn("not a runtime GitHub notice", operating)
        self.assertIn("does not call `human_only_notice()`", operating)

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
                self.assertIn("final Phase 0 gate", content)
                self.assertIn("does not call `human_only_notice()`", content)

    def test_github_only_route_does_not_require_providers(self):
        startup = (ROOT / "docs/PROJECT_STARTUP.md").read_text(encoding="utf-8")
        minimum = (ROOT / "docs/MINIMUM_SAFETY_PROFILE.md").read_text(encoding="utf-8")
        operating = (ROOT / "docs/OPERATING_RULES.md").read_text(encoding="utf-8")

        self.assertIn("Codex and Claude are optional helpers", startup)
        self.assertIn("For the GitHub-only route, no Claude credential is required", startup)
        self.assertIn("must not wait for provider setup or quota", operating)
        self.assertIn("Neither provider is required for implementation, review, or merge", minimum)
        self.assertIn("confirm no Codex or Claude response is required for completion", startup)

    def test_provider_setup_is_recorded_only_when_enabled(self):
        startup = (ROOT / "docs/PROJECT_STARTUP.md").read_text(encoding="utf-8")
        self.assertIn("optional Codex environment confirmation only when Codex was deliberately enabled", startup)
        self.assertIn("optional Claude Secret **name** confirmation only when Claude was deliberately enabled", startup)
        self.assertIn("never its value", startup)
        self.assertNotIn("Codex environment confirmed;", startup)
        self.assertNotIn("Secret **name** confirmed, never its value;", startup)

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

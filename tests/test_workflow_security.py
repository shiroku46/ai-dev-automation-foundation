import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def text(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def job_block(workflow: str, job: str, next_job: str | None = None) -> str:
    marker = f"\n  {job}:\n"
    block = workflow.split(marker, 1)[1]
    if next_job:
        block = block.split(f"\n  {next_job}:\n", 1)[0]
    return block


class WorkflowSecurityTest(unittest.TestCase):
    def test_all_workflows_have_required_sections_and_no_unsafe_trigger(self):
        workflows = sorted(WORKFLOWS.glob("*.yml"))
        self.assertTrue(workflows)
        for path in workflows:
            content = path.read_text(encoding="utf-8")
            self.assertTrue(content.startswith("name:"), path)
            self.assertIn("\non:\n", content, path)
            self.assertIn("\njobs:\n", content, path)
            self.assertNotIn("\t", content, path)
            self.assertNotIn("pull_request_target", content, path)

    def test_public_pull_request_jobs_are_read_only_and_secret_free(self):
        for name in ("ci.yml", "unit-tests.yml"):
            content = text(name)
            self.assertIn("permissions: {}", content)
            self.assertIn("permissions:\n      contents: read", content)
            self.assertIn("\n  pull_request:\n", content)
            self.assertNotIn("workflow_dispatch", content)
            self.assertNotIn("workflow_call", content)
            self.assertNotIn("secrets.", content)
            self.assertNotIn("id-token: write", content)
            self.assertNotIn("checks: write", content)
            self.assertNotIn("statuses: write", content)
            self.assertNotIn("actions: write", content)

    def test_ci_parses_complete_single_document_yaml_streams(self):
        content = text("ci.yml")
        self.assertIn('require "psych"', content)
        self.assertIn("Psych.parse_stream", content)
        self.assertIn("documents.length == 1", content)
        self.assertNotIn("YAML.safe_load", content)
        self.assertNotIn("pip install", content)

    def test_trusted_checks_separate_write_metadata_from_proposed_execution(self):
        content = text("trusted-checks.yml")
        self.assertIn("\n  workflow_call:\n", content)
        self.assertIn("CI / validate", content)
        self.assertIn("Unit Tests / test", content)
        self.assertIn("foundation:trusted-checks:", content)
        begin = job_block(content, "begin", "validate")
        validate = job_block(content, "validate", "test")
        test = job_block(content, "test", "finalize")
        finalize = job_block(content, "finalize")
        for block in (begin, finalize):
            self.assertIn("checks: write", block)
            self.assertNotIn("actions/checkout@", block)
            self.assertNotIn("python scripts/", block)
            self.assertNotIn("unittest", block)
        for block in (validate, test):
            self.assertIn("permissions:\n      contents: read", block)
            self.assertIn("actions/checkout@", block)
            self.assertIn("ref: ${{ inputs.target_sha }}", block)
            self.assertNotIn("checks: write", block)
            self.assertNotIn("id-token: write", block)
            self.assertNotIn("secrets.", block)

    def test_queue_owner_guard_and_bot_source_binding(self):
        content = text("claude-queue.yml")
        self.assertIn("github.actor == github.repository_owner", content)
        self.assertIn("github.actor == vars.AUTOMATION_OWNER", content)
        self.assertNotIn("github.triggering_actor", content)
        self.assertIn('body.strip() == trigger', content)
        self.assertIn("source_run_id", content)
        self.assertIn("actions/workflows/supervisor.yml", content)
        self.assertIn('source.get("head_branch") == default', content)
        self.assertIn('source.get("status") in {"queued", "in_progress"}', content)

    def test_reconciliation_is_fixed_bounded_and_default_branch_controlled(self):
        content = text("ci-reconcile.yml")
        self.assertIn('workflows: ["CI", "Unit Tests"]', content)
        self.assertIn("uses: ./.github/workflows/trusted-checks.yml", content)
        self.assertIn("max_candidates = 10", content)
        self.assertIn("max_attempts = 3", content)
        self.assertIn('allowed_authors = {owner, "github-actions[bot]"}', content)
        self.assertIn('base.get("ref") != default', content)
        self.assertIn('run.get("event") == "workflow_call"', content)
        self.assertIn('run.get("head_branch") == default', content)
        self.assertNotIn("gh workflow run", content)
        self.assertNotIn("statuses: write", content)
        self.assertNotIn("/statuses/", content)

    def test_supervisor_workflow_uses_only_default_branch_code(self):
        content = text("supervisor.yml")
        self.assertNotIn("\n  pull_request:\n", content)
        self.assertIn('workflows: ["Trusted Exact-SHA Checks"]', content)
        self.assertIn("ref: ${{ github.event.repository.default_branch }}", content)
        self.assertIn("actions: read", content)
        self.assertIn("checks: read", content)
        self.assertNotIn("github.event.pull_request.head.sha", content)

    def test_runtime_rejects_native_or_forged_checks_and_nondefault_targets(self):
        runtime = (ROOT / "scripts/supervisor_runtime.py").read_text(encoding="utf-8")
        self.assertIn('get("slug") != "github-actions"', runtime)
        self.assertIn('run.get("event") != "workflow_call"', runtime)
        self.assertIn('run.get("head_branch") != DEFAULT_BRANCH', runtime)
        self.assertIn('run.get("display_title") != f"Trusted checks {sha}"', runtime)
        self.assertIn("foundation:trusted-checks:", runtime)
        self.assertIn('base.get("ref") != DEFAULT_BRANCH', runtime)
        self.assertIn("MAX_CHANGED_FILES = 100", runtime)
        self.assertIn("exact_codex_clean", runtime)
        self.assertIn("merge_method=squash", runtime)
        self.assertIn('f"sha={sha}"', runtime)
        self.assertNotIn("/commits/{sha}/status", runtime)

    def test_bootstrap_distributes_trusted_check_workflow(self):
        generator = (ROOT / "bootstrap/generator.py").read_text(encoding="utf-8")
        validator = (ROOT / "scripts/validate_repository.py").read_text(encoding="utf-8")
        self.assertIn('".github/workflows/trusted-checks.yml"', generator)
        self.assertIn('".github/workflows/trusted-checks.yml"', validator)


if __name__ == "__main__":
    unittest.main()

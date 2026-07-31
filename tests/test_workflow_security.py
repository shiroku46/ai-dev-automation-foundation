import importlib
import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PIN = re.compile(r"uses:\s*[^@\s]+@[0-9a-f]{40}\s*$")


class WorkflowSecurityTest(unittest.TestCase):
    def test_all_workflows_have_required_sections_and_pinned_actions(self):
        workflows = sorted((ROOT / ".github/workflows").glob("*.yml"))
        self.assertTrue(workflows)
        for path in workflows:
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("name:"), path)
            self.assertIn("\non:\n", text, path)
            self.assertIn("\njobs:\n", text, path)
            self.assertNotIn("\t", text, path)
            self.assertNotIn("pull_request_target", text, path)
            for line in text.splitlines():
                if line.strip().removeprefix("- ").startswith("uses:"):
                    self.assertRegex(line, PIN, path)

    def test_contributor_checks_are_read_only_secret_free_and_exact_sha(self):
        for name in ("ci.yml", "unit-tests.yml"):
            text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
            self.assertIn("permissions:\n  contents: read", text)
            self.assertIn("github.event.pull_request.head.sha", text)
            self.assertIn("persist-credentials: false", text)
            self.assertNotIn("secrets.", text)
            self.assertNotIn("id-token: write", text)
            self.assertNotIn("checks: write", text)
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("Psych.parse_stream", ci)
        self.assertIn("documents.length == 1", ci)
        self.assertNotIn("pip install", ci)

    def test_trusted_attestation_is_candidate_bound_and_separates_permissions(self):
        text = (ROOT / ".github/workflows/trusted-checks.yml").read_text(encoding="utf-8")
        self.assertIn("run-name: Trusted checks ${{ inputs.target_sha }}", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("workflow_call:", text)
        self.assertIn("WORKFLOW_REF: ${{ github.workflow_ref }}", text)
        self.assertIn("WORKFLOW_SHA: ${{ github.workflow_sha }}", text)
        self.assertNotIn("job.workflow_ref", text)
        self.assertNotIn("job.workflow_sha", text)
        self.assertIn("CI / validate", text)
        self.assertIn("Unit Tests / test", text)
        self.assertIn("head_sha=$TARGET_SHA", text)
        self.assertIn("status=in_progress", text)
        self.assertIn("status=completed", text)
        self.assertIn('f"repos/{repository}/commits/{target_sha}/pulls?per_page=100"', text)
        self.assertIn('head.get("sha") != target_sha', text)
        self.assertIn('base.get("ref") != default_branch', text)
        self.assertIn('author not in allowed_authors', text)
        self.assertIn('"ai-no-merge" in labels', text)

        authorize = text.split("  authorize:\n", 1)[1].split("  validate_target:\n", 1)[0]
        validate = text.split("  validate_target:\n", 1)[1].split("  test_target:\n", 1)[0]
        test = text.split("  test_target:\n", 1)[1].split("  finalize:\n", 1)[0]
        finalize = text.split("  finalize:\n", 1)[1]
        self.assertIn("pull-requests: read", authorize)
        for metadata_job in (authorize, finalize):
            self.assertIn("checks: write", metadata_job)
            self.assertNotIn("actions/checkout", metadata_job)
            self.assertNotIn("python scripts/", metadata_job)
            self.assertNotIn("unittest", metadata_job)
        for proposed_code_job in (validate, test):
            self.assertIn("permissions:\n      contents: read", proposed_code_job)
            self.assertIn("actions/checkout", proposed_code_job)
            self.assertIn('test "$(git rev-parse HEAD)" = "$TARGET_SHA"', proposed_code_job)
            self.assertNotIn("checks: write", proposed_code_job)
            self.assertNotIn("id-token: write", proposed_code_job)
            self.assertNotIn("secrets.", proposed_code_job)

    def test_queue_owner_and_bot_dispatch_are_default_branch_bound(self):
        text = (ROOT / ".github/workflows/claude-queue.yml").read_text(encoding="utf-8")
        self.assertIn("github.actor == github.repository_owner", text)
        self.assertIn("github.actor == vars.AUTOMATION_OWNER", text)
        self.assertIn("github.actor == 'github-actions[bot]'", text)
        self.assertIn("trusted_run_id:", text)
        self.assertIn('expected_path = f".github/workflows/supervisor.yml@{default_branch}"', text)
        self.assertIn("github.ref_name == github.event.repository.default_branch", text)
        self.assertIn('body.strip() == trigger', text)
        self.assertNotIn("github.triggering_actor", text)

    def test_reconciliation_dispatches_only_fixed_candidate_bound_workflow(self):
        text = (ROOT / ".github/workflows/ci-reconcile.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_run:", text)
        self.assertIn("python -m scripts.supervisor_runtime discover", text)
        self.assertIn("max-parallel: 2", text)
        self.assertIn("actions: write", text)
        self.assertIn("gh workflow run trusted-checks.yml", text)
        self.assertIn('--ref "$DEFAULT_BRANCH"', text)
        self.assertIn('-f "target_sha=$TARGET_SHA"', text)
        self.assertNotIn("statuses: write", text)
        self.assertNotIn("pull_request:\n", text)
        self.assertNotIn("uses: ./.github/workflows/", text)

    def test_supervisor_requires_fixed_candidate_bound_run_and_codex_evidence(self):
        workflow = (ROOT / ".github/workflows/supervisor.yml").read_text(encoding="utf-8")
        runtime = (ROOT / "scripts/supervisor_runtime.py").read_text(encoding="utf-8")
        self.assertIn("ref: ${{ github.event.repository.default_branch }}", workflow)
        self.assertIn(
            'workflows: ["CI", "Unit Tests", "Trusted CI Reconciliation", "Trusted Exact-SHA Checks"]',
            workflow,
        )
        self.assertNotIn("pull_request:\n", workflow)
        for required in (
            "trusted_workflow_id()",
            "current_default_sha()",
            'run.get("event") != "workflow_dispatch"',
            'run.get("display_title") != f"Trusted checks {sha}"',
            'f"foundation:{run_id}:{name}:{sha}"',
            "reviewThreads(first:100,after:$cursor)",
            "hasNextPage",
            "marker = f\"<!-- foundation-codex-request:{sha} -->\"",
            "login == ACTIONS_LOGIN",
            'item.get("created_at") == item.get("updated_at")',
            "MAX_ATTESTATION_ATTEMPTS",
            "api_key_pages",
            "api_list",
            "previous_filename",
            "trusted_runs_for_sha",
        ):
            self.assertIn(required, runtime)
        self.assertNotIn("@lru_cache(maxsize=1)\ndef current_default_sha", runtime)
        self.assertNotIn("referenced_workflows", runtime)
        self.assertNotIn("/commits/{sha}/status", runtime)

    def test_codex_request_deduplication_ignores_untrusted_marker_comments(self):
        runtime = (ROOT / "scripts/supervisor_runtime.py").read_text(encoding="utf-8")
        request_function = runtime.split("def request_codex", 1)[1].split("def supervise", 1)[0]
        self.assertIn('get("login") == ACTIONS_LOGIN', request_function)
        self.assertIn('item.get("created_at") == item.get("updated_at")', request_function)

    def test_source_issue_and_negative_e2e_close_are_owner_trusted(self):
        runtime = (ROOT / "scripts/supervisor_runtime.py").read_text(encoding="utf-8")
        self.assertIn("TRUSTED_ISSUE_AUTHORS", runtime)
        self.assertIn('not issue.get("pull_request")', runtime)
        self.assertIn('E2E_AUTO_CLOSE_MARKER = "<!-- foundation-e2e-auto-close -->"', runtime)
        self.assertIn("E2E_AUTO_CLOSE_MARKER in issue_body", runtime)
        self.assertIn('"UNTRUSTED_SOURCE_ISSUE"', runtime)

    def test_runtime_candidate_and_details_url_policy(self):
        environment = {
            "REPOSITORY": "example/foundation",
            "DEFAULT_BRANCH": "main",
            "AUTOMATION_OWNER": "owner",
        }
        with patch.dict(os.environ, environment, clear=False):
            sys.modules.pop("scripts.supervisor_runtime", None)
            runtime = importlib.import_module("scripts.supervisor_runtime")

        trusted = {
            "head": {"ref": "automation/probe", "repo": {"full_name": "example/foundation"}},
            "base": {"ref": "main", "repo": {"full_name": "example/foundation"}},
            "user": {"login": "owner"},
            "labels": [],
        }
        self.assertTrue(runtime.trusted_candidate(trusted))
        forked = {**trusted, "head": {"ref": "automation/probe", "repo": {"full_name": "fork/repo"}}}
        self.assertFalse(runtime.trusted_candidate(forked))
        wrong_base = {**trusted, "base": {"ref": "other", "repo": {"full_name": "example/foundation"}}}
        self.assertFalse(runtime.trusted_candidate(wrong_base))
        untrusted_author = {**trusted, "user": {"login": "contributor"}}
        self.assertFalse(runtime.trusted_candidate(untrusted_author))
        run_base = "https://github.com/" + "example/foundation/" + "actions/runs/"
        self.assertEqual(runtime.run_id_from_details_url(run_base + "12345"), 12345)
        self.assertIsNone(runtime.run_id_from_details_url(run_base + "12345/job/7"))


if __name__ == "__main__":
    unittest.main()

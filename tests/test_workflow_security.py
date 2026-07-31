import importlib
import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PIN = re.compile(r"uses:\s*[^@\s]+@[0-9a-f]{40}\s*$")
LOCAL_REUSABLE = "uses: ./.github/workflows/trusted-checks.yml"


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
                stripped = line.strip().removeprefix("- ")
                if stripped.startswith("uses:") and stripped != LOCAL_REUSABLE:
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

    def test_trusted_attestation_separates_metadata_writes_from_proposed_code(self):
        text = (ROOT / ".github/workflows/trusted-checks.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_call:", text)
        self.assertIn("job.workflow_ref", text)
        self.assertIn("job.workflow_sha", text)
        self.assertIn("CI / validate", text)
        self.assertIn("Unit Tests / test", text)
        self.assertIn("head_sha=$TARGET_SHA", text)
        self.assertIn("status=in_progress", text)
        self.assertIn("status=completed", text)

        authorize = text.split("  authorize:\n", 1)[1].split("  validate_target:\n", 1)[0]
        validate = text.split("  validate_target:\n", 1)[1].split("  test_target:\n", 1)[0]
        test = text.split("  test_target:\n", 1)[1].split("  finalize:\n", 1)[0]
        finalize = text.split("  finalize:\n", 1)[1]
        self.assertIn("checks: write", authorize)
        self.assertIn("checks: write", finalize)
        self.assertNotIn("actions/checkout", authorize)
        self.assertNotIn("actions/checkout", finalize)
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

    def test_reconciliation_calls_only_fixed_local_reusable_workflow(self):
        text = (ROOT / ".github/workflows/ci-reconcile.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_run:", text)
        self.assertIn("python -m scripts.supervisor_runtime discover", text)
        self.assertIn(LOCAL_REUSABLE, text)
        self.assertIn("max-parallel: 2", text)
        self.assertIn("checks: write", text)
        self.assertNotIn("gh workflow run", text)
        self.assertNotIn("statuses: write", text)
        self.assertNotIn("pull_request:", text)

    def test_supervisor_requires_fixed_attestation_and_immutable_codex_evidence(self):
        workflow = (ROOT / ".github/workflows/supervisor.yml").read_text(encoding="utf-8")
        runtime = (ROOT / "scripts/supervisor_runtime.py").read_text(encoding="utf-8")
        self.assertIn("ref: ${{ github.event.repository.default_branch }}", workflow)
        self.assertIn('workflows: ["CI", "Unit Tests", "Trusted CI Reconciliation"]', workflow)
        self.assertNotIn("pull_request:\n", workflow)
        for required in (
            "referenced_workflows",
            "current_default_sha()",
            "expected_external_id",
            "run.get(\"path\") != expected_caller",
            "reviewThreads(first:100,after:$cursor)",
            "hasNextPage",
            "expected_marker",
            "login == ACTIONS_LOGIN",
            'item.get("created_at") == item.get("updated_at")',
            "MAX_ATTESTATION_ATTEMPTS",
            "api_key_pages",
            "api_list",
        ):
            self.assertIn(required, runtime)
        self.assertNotIn("/commits/{sha}/status", runtime)

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
            "REPOSITORY_OWNER": "owner",
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

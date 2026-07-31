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

    def test_trusted_attestation_uses_github_owned_run_job_evidence(self):
        text = (ROOT / ".github/workflows/trusted-checks.yml").read_text(encoding="utf-8")
        self.assertIn("run-name: Trusted checks ${{ inputs.target_sha }}", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("workflow_call:", text)
        self.assertIn("WORKFLOW_REF: ${{ github.workflow_ref }}", text)
        self.assertIn("WORKFLOW_SHA: ${{ github.workflow_sha }}", text)
        self.assertIn("name: CI / validate", text)
        self.assertIn("name: Unit Tests / test", text)
        self.assertNotIn("checks: write", text)
        self.assertNotIn("/check-runs", text)
        self.assertNotIn('"external_id"', text)
        self.assertNotIn("finalize:", text)

        authorize = text.split("  authorize:\n", 1)[1].split("  validate_target:\n", 1)[0]
        validate = text.split("  validate_target:\n", 1)[1].split("  test_target:\n", 1)[0]
        test = text.split("  test_target:\n", 1)[1]

        self.assertIn("contents: read", authorize)
        self.assertIn("pull-requests: read", authorize)
        self.assertNotIn("issues: write", authorize)
        self.assertNotIn("checks: write", authorize)
        self.assertNotIn("actions/checkout", authorize)
        self.assertIn("pr_number=", authorize)

        for proposed_code_job in (validate, test):
            self.assertIn("permissions:\n      contents: read", proposed_code_job)
            self.assertIn("actions/checkout", proposed_code_job)
            self.assertIn('test "$(git rev-parse HEAD)" = "$TARGET_SHA"', proposed_code_job)
            self.assertIn("persist-credentials: false", proposed_code_job)
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

    def test_supervisor_uses_exact_run_job_attestation_and_provenance_gates(self):
        workflow = (ROOT / ".github/workflows/supervisor.yml").read_text(encoding="utf-8")
        runtime = (ROOT / "scripts/supervisor_runtime.py").read_text(encoding="utf-8")
        self.assertIn("ref: ${{ github.event.repository.default_branch }}", workflow)
        self.assertIn("Trusted Exact-SHA Checks", workflow)
        self.assertNotIn("pull_request:\n", workflow)
        for required in (
            "trusted_workflow_id()",
            "current_default_sha()",
            'run.get("event") != "workflow_dispatch"',
            'run.get("display_title") != f"Trusted checks {sha}"',
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
            "trusted_run_jobs",
            "ATTESTATION_JOB_NAMES",
            "_complete_successful_job_set",
            'actions/runs/{run_id}/jobs?filter=all',
        ):
            self.assertIn(required, runtime)
        self.assertNotIn("@lru_cache(maxsize=1)\ndef current_default_sha", runtime)
        self.assertNotIn("/check-runs", runtime)
        self.assertNotIn("external_id", runtime)
        self.assertNotIn("run_id_from_details_url", runtime)
        self.assertNotIn("/commits/{sha}/status", runtime)

    def test_codex_request_deduplication_ignores_untrusted_marker_comments(self):
        runtime = (ROOT / "scripts/supervisor_runtime.py").read_text(encoding="utf-8")
        request_section = runtime.split("def _codex_request_exists", 1)[1].split("def supervise", 1)[0]
        self.assertIn('get("login") == ACTIONS_LOGIN', request_section)
        self.assertIn('item.get("created_at") == item.get("updated_at")', request_section)
        self.assertIn("if _codex_request_exists(pr_number, sha):", request_section)

    def test_source_issue_and_negative_e2e_close_are_owner_trusted(self):
        runtime = (ROOT / "scripts/supervisor_runtime.py").read_text(encoding="utf-8")
        self.assertIn("TRUSTED_ISSUE_AUTHORS", runtime)
        self.assertIn('not issue.get("pull_request")', runtime)
        self.assertIn('E2E_AUTO_CLOSE_MARKER = "<!-- foundation-e2e-auto-close -->"', runtime)
        self.assertIn("E2E_AUTO_CLOSE_MARKER in issue_body", runtime)
        self.assertIn('"UNTRUSTED_SOURCE_ISSUE"', runtime)

    def test_runtime_candidate_policy(self):
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


if __name__ == "__main__":
    unittest.main()

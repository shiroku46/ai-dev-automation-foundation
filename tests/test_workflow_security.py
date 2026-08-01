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
            self.assertNotIn("contents: write", text)
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
        self.assertNotIn("actions/checkout", authorize)
        for proposed_code_job in (validate, test):
            self.assertIn("permissions:\n      contents: read", proposed_code_job)
            self.assertIn("actions/checkout", proposed_code_job)
            self.assertIn('test "$(git rev-parse HEAD)" = "$TARGET_SHA"', proposed_code_job)
            self.assertIn("persist-credentials: false", proposed_code_job)
            self.assertNotIn("checks: write", proposed_code_job)
            self.assertNotIn("id-token: write", proposed_code_job)
            self.assertNotIn("secrets.", proposed_code_job)

    def test_queue_and_reconciliation_are_fixed_default_branch_paths(self):
        queue = (ROOT / ".github/workflows/claude-queue.yml").read_text(encoding="utf-8")
        self.assertIn("github.actor == github.repository_owner", queue)
        self.assertIn("github.actor == vars.AUTOMATION_OWNER", queue)
        self.assertIn("github.actor == 'github-actions[bot]'", queue)
        self.assertIn("trusted_run_id:", queue)
        self.assertIn('expected_path = f".github/workflows/supervisor.yml@{default_branch}"', queue)
        self.assertIn('body.strip() == trigger', queue)
        self.assertNotIn("github.triggering_actor", queue)

        reconcile = (ROOT / ".github/workflows/ci-reconcile.yml").read_text(encoding="utf-8")
        self.assertIn('workflows: ["CI", "Unit Tests", "Claude Issue Queue"]', reconcile)
        self.assertIn("python -m scripts.supervisor_runtime discover", reconcile)
        self.assertIn("max-parallel: 2", reconcile)
        self.assertIn("gh workflow run trusted-checks.yml", reconcile)
        self.assertIn('--ref "$DEFAULT_BRANCH"', reconcile)
        self.assertIn('-f "target_sha=$TARGET_SHA"', reconcile)
        self.assertIn("\n  queue_recovery:\n", reconcile)
        recovery = reconcile.split("\n  queue_recovery:\n", 1)[1]
        self.assertIn("actions: write", recovery)
        self.assertIn("contents: write", recovery)
        self.assertIn("issues: read", recovery)
        self.assertIn("pull-requests: read", recovery)
        self.assertIn("ref: ${{ github.event.repository.default_branch }}", recovery)
        self.assertIn("persist-credentials: false", recovery)
        self.assertIn("python -m scripts.supervisor_queue_recovery_v3", recovery)
        self.assertNotIn("statuses: write", reconcile)
        self.assertNotIn("pull_request:\n", reconcile)
        self.assertNotIn("secrets.", reconcile)
        self.assertNotIn("id-token: write", reconcile)

    def test_supervisor_has_only_bounded_default_branch_write_path(self):
        workflow = (ROOT / ".github/workflows/supervisor.yml").read_text(encoding="utf-8")
        self.assertIn("ref: ${{ github.event.repository.default_branch }}", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("Trusted Exact-SHA Checks", workflow)
        self.assertIn("actions: read", workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn("python -m scripts.supervisor_final_guard", workflow)
        self.assertNotIn("actions: write", workflow)
        self.assertNotIn("python -m scripts.supervisor_queue_recovery_v3", workflow)
        self.assertNotIn("pull_request:\n", workflow)
        self.assertNotIn("secrets.", workflow)
        self.assertNotIn("id-token: write", workflow)

    def test_runtime_preserves_exact_sha_provenance_and_merge_gates(self):
        runtime = (ROOT / "scripts/supervisor_runtime.py").read_text(encoding="utf-8")
        for required in (
            "trusted_workflow_id()",
            "current_default_sha()",
            'run.get("event") != "workflow_dispatch"',
            'run.get("display_title") != f"Trusted checks {sha}"',
            "reviewThreads(first:100,after:$cursor)",
            "hasNextPage",
            "MAX_ATTESTATION_ATTEMPTS",
            "api_key_pages",
            "previous_filename",
            "trusted_runs_for_sha",
            "trusted_run_jobs",
            "ATTESTATION_JOB_NAMES",
            "_complete_successful_job_set",
            'actions/runs/{run_id}/jobs?filter=all',
            "merge_method=squash",
            'f"sha={sha}"',
        ):
            self.assertIn(required, runtime)
        self.assertNotIn("external_id", runtime)
        self.assertNotIn("run_id_from_details_url", runtime)
        self.assertNotIn("/commits/{sha}/status", runtime)

    def test_internal_stop_storage_is_non_commenting_and_deterministic(self):
        runtime = (ROOT / "scripts/supervisor_runtime.py").read_text(encoding="utf-8")
        for required in (
            'INTERNAL_STOP_BRANCH = "automation-internal-stops"',
            'INTERNAL_STOP_ROOT = "automation-stops"',
            "def internal_stop_record_path(",
            "def canonical_internal_stop_record(",
            "def ensure_internal_stop_branch(",
            "def persist_internal_stop_record(",
            '"notification": False',
            '"required_human_action": None',
            "self_resolution_audit",
            'f"repos/{REPO}/commits/{sha}/check-runs?per_page=100"',
            'f"repos/{REPO}/collaborators/{AUTOMATION_OWNER}/permission"',
            "AUDIT_WORKFLOWS",
            "initial_and_final_head_confirmed=true",
        ):
            self.assertIn(required, runtime)
        stop = runtime.split("def stop_report(", 1)[1].split("\ndef format_human_only_notice(", 1)[0]
        self.assertIn("persist_internal_stop_record", stop)
        self.assertNotIn("comment(", stop)
        self.assertNotIn("/comments", stop)
        self.assertNotIn("gh issue comment", stop)

    def test_no_progress_uses_immutable_evidence_timestamps(self):
        runtime = (ROOT / "scripts/supervisor_runtime.py").read_text(encoding="utf-8")
        supervise = runtime.split("def supervise(", 1)[1].split("\ndef main(", 1)[0]
        self.assertIn('minutes_since(codex.get("request_timestamp"))', supervise)
        self.assertIn("latest_successful_attestation_timestamp(attempts)", supervise)
        self.assertNotIn('pr.get("updated_at")', supervise)
        self.assertNotIn('pr["updated_at"]', supervise)
        self.assertIn("exact_codex_evidence", runtime)
        self.assertIn("request_timestamp", runtime)

    def test_human_only_notice_is_connected_audited_and_trusted_bot_deduped(self):
        runtime = (ROOT / "scripts/supervisor_runtime.py").read_text(encoding="utf-8")
        for reason in (
            "HUMAN_ONLY_ACCOUNT_LEVEL_REPOSITORY_CREATION_UI_UNAVAILABLE",
            "HUMAN_ONLY_CREDENTIAL_PROVIDER_UI_REQUIRED",
            "HUMAN_ONLY_DISCONNECTED_INTEGRATION_RECONNECTION_UI_REQUIRED",
        ):
            self.assertIn(reason, runtime)
        notice = runtime.split("def human_only_notice(", 1)[1].split("\ndef discover_targets(", 1)[0]
        for required in (
            "format_human_only_notice",
            "_validated_notice_destination",
            "self_resolution_audit",
            "ACTIONS_LOGIN",
            'item.get("created_at") == item.get("updated_at")',
            "marker",
        ):
            self.assertIn(required, notice)

    def test_guidance_and_bootstrap_have_policy_parity(self):
        operating = (ROOT / "docs/OPERATING_RULES.md").read_text(encoding="utf-8")
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        generator = (ROOT / "bootstrap/generator.py").read_text(encoding="utf-8")
        for text in (operating, agents, claude, generator):
            self.assertIn("automation-internal-stops", text)
        self.assertIn("never posted as Issue or Pull Request comments", operating)
        self.assertIn("immutable trusted exact-SHA request comment", operating)
        self.assertIn("github-actions[bot]", operating)
        self.assertIn("automatic-resumption condition", operating)

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

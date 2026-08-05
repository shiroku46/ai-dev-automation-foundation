"""Integration-level contracts for the optional Queue and GitHub final guard.

Legacy provider-driven Git Data publication and provider-specific recovery are
intentionally absent. Verified optional-provider artifacts hand off to the
GitHub-direct coordinator; readiness and merge remain default-branch controlled.
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def workflow(name: str) -> str:
    return (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")


def job_block(content: str, name: str, following: str | None = None) -> str:
    marker = f"\n  {name}:\n"
    if marker not in content:
        raise AssertionError(f"missing job {name}")
    block = content.split(marker, 1)[1]
    if following:
        block = block.split(f"\n  {following}:\n", 1)[0]
    return block


class QueueAndFinalGuardTest(unittest.TestCase):
    def test_optional_queue_has_no_ordinary_provider_trigger(self):
        queue = workflow("claude-queue.yml")
        self.assertIn("issue_comment:\n    types: [created]", queue)
        self.assertIn("workflow_dispatch:", queue)
        self.assertNotIn("\n  issues:\n", queue)
        self.assertNotIn("workflow_run:", queue)
        self.assertNotIn("schedule:", queue)
        self.assertIn("from scripts.queue_event_guard import resolve_queue_event", queue)
        self.assertNotIn("EVENT_PATH", queue)
        self.assertNotIn("github.event_path", queue)

    def test_permission_contract_precedes_provider_invocation(self):
        queue = workflow("claude-queue.yml")
        prepare = job_block(queue, "prepare", "implement")
        self.assertIn("check_tool_permission_contract", prepare)
        self.assertIn("foundation-provider-required-commands", prepare)
        self.assertIn("contract_ok", prepare)
        self.assertIn("model_invocation: `skipped`", prepare)
        self.assertIn("notification: false", prepare)
        self.assertIn("human_action_required: false", prepare)

    def test_provider_job_is_explicitly_unavailable_and_credential_free(self):
        queue = workflow("claude-queue.yml")
        implement = job_block(queue, "implement", "verify")
        for required in (
            "timeout-minutes: 5", "permissions: {}", "Optional provider missing secret",
            "credential-isolated route unavailable", "provider_invocation: false",
            "repository_credentials_exposed: false", "repository_write: false",
            "notification: false", "human_action_required: false", "exit 1",
        ):
            self.assertIn(required, implement)
        for forbidden in (
            "continue-on-error: true", "contents: read", "issues: read", "pull-requests: read",
            "id-token: write", "persist-credentials", "track_progress", "allowed_bots",
            "anthropics/", "secrets.", "GH_TOKEN", "github.token", "actions/checkout@",
            "contents: write", "issues: write", "pull-requests: write",
        ):
            self.assertNotIn(forbidden, implement)

    def test_unavailable_provider_creates_no_checkpoint_or_artifact(self):
        queue = workflow("claude-queue.yml")
        implement = job_block(queue, "implement", "verify")
        self.assertIn('checkpoint_kind: ${{ steps.unavailable.outputs.checkpoint_kind }}', implement)
        self.assertIn('artifact_sha256: ${{ steps.unavailable.outputs.artifact_sha256 }}', implement)
        self.assertIn('echo "checkpoint_kind="', implement)
        self.assertIn('echo "artifact_sha256="', implement)
        for forbidden in (
            '"complete" if', 'else "wip"', "retry_identity", "changed_paths",
            "patch_sha256", "empty or unauthorized checkpoint",
            "checkpoint leaves must be regular files", "actions/upload-artifact@",
            "retention-days: 1", "queue-checkpoint", "candidate.patch", "checkpoint.json",
            "git add", "git diff", "git push",
        ):
            self.assertNotIn(forbidden, implement)

    def test_verification_is_read_only_and_secret_free(self):
        queue = workflow("claude-queue.yml")
        verify = job_block(queue, "verify", "publish")
        self.assertIn("permissions:\n      contents: read", verify)
        self.assertIn("persist-credentials: false", verify)
        self.assertIn("python scripts/public_export_guard.py .", verify)
        self.assertIn("python scripts/validate_repository.py", verify)
        for forbidden in ("secrets.", "id-token: write", "contents: write", "pull-requests: write"):
            self.assertNotIn(forbidden, verify)

    def test_verified_artifact_handoff_has_zero_repository_write(self):
        queue = workflow("claude-queue.yml")
        publish = job_block(queue, "publish", "finalize")
        self.assertIn("permissions:\n      contents: read", publish)
        self.assertIn("publication_route: GitHub-direct coordinator", publish)
        self.assertIn("repository_write: false", publish)
        for forbidden in (
            "contents: write",
            "pull-requests: write",
            "issues: write",
            "secrets.",
            "id-token: write",
            "repos/{repo}/git/",
            '"PATCH"',
        ):
            self.assertNotIn(forbidden, publish)

    def test_final_optional_state_is_non_notifying(self):
        finalize = job_block(workflow("claude-queue.yml"), "finalize")
        self.assertIn("notification: false", finalize)
        self.assertIn("human_action_required: false", finalize)
        self.assertIn("Continue GitHub-direct work", finalize)
        self.assertNotIn("gh issue comment", finalize)
        self.assertNotIn("ai-blocked", finalize)

    def test_supervisor_is_the_only_readiness_and_merge_route(self):
        supervisor = workflow("supervisor.yml")
        self.assertIn("python -m scripts.github_coordinator_supervisor", supervisor)
        self.assertIn('workflows: ["CI", "Unit Tests"]', supervisor)
        self.assertIn("issue_comment:", supervisor)
        self.assertIn("schedule:", supervisor)
        for forbidden in (
            "Claude Issue Queue",
            "supervisor_final_guard",
            "supervisor_queue_recovery",
            "anthropics/",
            "secrets.",
            "id-token: write",
        ):
            self.assertNotIn(forbidden, supervisor)

    def test_ci_reconciliation_separates_observation_and_bounded_recovery(self):
        reconcile = workflow("ci-reconcile.yml")
        self.assertIn('workflows: ["CI", "Unit Tests", "Claude Issue Queue"]', reconcile)
        self.assertIn("schedule:", reconcile)
        self.assertIn("cancel-in-progress: false", reconcile)

        observe = job_block(reconcile, "observe", "queue_recovery")
        self.assertIn("actions: read", observe)
        self.assertIn("contents: read", observe)
        self.assertIn("pull-requests: read", observe)
        self.assertIn("read-only compatibility observation", observe)
        self.assertIn("provider_invocation: false", observe)
        self.assertIn("human_action_required: false", observe)
        for forbidden in (
            "actions: write",
            "contents: write",
            "issues: write",
            "pull-requests: write",
        ):
            self.assertNotIn(forbidden, observe)

        recovery = job_block(reconcile, "queue_recovery")
        for required in (
            "actions: write",
            "contents: write",
            "issues: read",
            "pull-requests: write",
            "max_retries = 3",
            "should_auto_retry",
            "candidate_execution_with_write_token: `false`",
            "notification: `false`",
            "human_action_required: `false`",
        ):
            self.assertIn(required, recovery)
        for forbidden in (
            "secrets.",
            "id-token: write",
            "anthropics/",
            "codex",
            "gh issue comment",
            "issues: write",
        ):
            self.assertNotIn(forbidden, recovery)

    def test_recovery_identity_and_failure_classification_fail_closed(self):
        recovery = job_block(workflow("ci-reconcile.yml"), "queue_recovery")
        body_trigger = recovery.split('if first == "/claude-run":', 1)[1].split(
            "              number = int(issue.get", 1
        )[0]
        self.assertIn('"created_at": str(issue.get("created_at") or "")', body_trigger)
        self.assertNotIn('"updated_at"', body_trigger)
        self.assertIn('"bad credentials"', recovery)
        self.assertIn('"http 401"', recovery)
        self.assertIn('"http 403"', recovery)
        self.assertNotIn('"missing secret", "unauthorized"', recovery)
        self.assertIn('"human_action_required": False', recovery)
        self.assertIn("optional provider route unavailable; continue GitHub-direct work", recovery)
        self.assertIn("len(files) >= 300", recovery)
        self.assertIn("remote Queue checkpoint changed-file evidence is incomplete", recovery)
        self.assertIn('for name in ("internal-stop.json", "exhausted.json")', recovery)

    def test_scheduled_recovery_reuses_branch_or_artifact_before_retry(self):
        recovery = job_block(workflow("ci-reconcile.yml"), "queue_recovery")
        self.assertIn("def latest_verified_artifact", recovery)
        self.assertIn('run.get("head_sha") == base_sha', recovery)
        self.assertIn("artifact = verify_artifact(run, issue)", recovery)
        schedule_path = recovery.split("          else:\n              if not active_queue_run():", 1)[1]
        branch_position = schedule_path.index("resumed = resume_remote_branch")
        artifact_position = schedule_path.index("recovered = latest_verified_artifact")
        retry_position = schedule_path.index("dispatch_retry(issue, base_sha, FailureClass.UNKNOWN, None)")
        self.assertLess(branch_position, artifact_position)
        self.assertLess(artifact_position, retry_position)

    def test_automation_dispatch_requires_exact_persisted_retry_intent(self):
        queue = workflow("claude-queue.yml")
        prepare = job_block(queue, "prepare", "implement")
        recovery = job_block(workflow("ci-reconcile.yml"), "queue_recovery")
        for required in (
            "request_fingerprint:",
            "retry_attempt:",
            "from scripts.queue_event_guard import resolve_queue_event",
            "decision.fingerprint",
            "decision.retry_attempt",
            "automation-stops/queue-v4/issue-",
            "automation-internal-stops",
            'record.get("next_automatic_action") == "dispatch one optional Queue retry"',
            "should_auto_retry(failure_class, attempt - 1, 3)",
            'RUN_ATTEMPT: ${{ github.run_attempt }}',
            "COMMENT_ISSUE:", "COMMENT_BODY:", "COMMENT_IS_PR:",
            '{"internal-stop.json", "exhausted.json"}',
        ):
            self.assertIn(required, queue)
        self.assertIn("decision = resolve_queue_event(", prepare)
        self.assertIn("decision.allowed", prepare)
        self.assertIn("decision.automated_retry", prepare)
        self.assertIn("if fingerprint_input != fingerprint", prepare)
        self.assertNotIn("not fingerprint_input or", prepare)
        self.assertNotIn("if attempt_input:\n                      attempts", prepare)
        self.assertIn('f"request_fingerprint={fingerprint}"', recovery)
        self.assertIn('f"retry_attempt={attempt}"', recovery)
        self.assertNotIn("contents: write", prepare)
        self.assertNotIn("id-token: write", prepare)


if __name__ == "__main__":
    unittest.main()

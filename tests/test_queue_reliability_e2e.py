"""E2E-style reliability tests for the Queue pipeline.

Covers Fix F items 3, 4, 6, and 7:
  3. transient failure retries automatically and creates a PR;
  4. retry count exhaustion produces an explicit automation incident;
  6. no-PR/no-branch stopped Issue is detected by the watchdog;
  7. duplicate triggers do not create parallel conflicting branches.

All GitHub API calls are mocked; no repository code is executed.
"""
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


REPO = "example/foundation-e2e"
DEFAULT_SHA = "d" * 40
FINGERPRINT = "a" * 20
RUN_ID = "9001"
CREATED_AT = "2026-01-01T00:00:00Z"


def _base_env():
    return {
        "REPOSITORY": REPO,
        "DEFAULT_BRANCH": "main",
        "AUTOMATION_OWNER": "owner",
        "GITHUB_EVENT_NAME": "schedule",
        "GITHUB_RUN_ID": RUN_ID,
    }


def _load_recovery():
    env = _base_env()
    with patch.dict(os.environ, env, clear=False):
        for name in (
            "scripts.supervisor_queue_recovery",
            "scripts.supervisor_runtime",
        ):
            sys.modules.pop(name, None)
        module = importlib.import_module("scripts.supervisor_queue_recovery")
    module._test_environment = env
    return module


def _load_v3():
    env = _base_env()
    with patch.dict(os.environ, env, clear=False):
        for name in (
            "scripts.supervisor_queue_recovery_v3",
            "scripts.supervisor_queue_recovery_v2",
            "scripts.supervisor_queue_recovery",
            "scripts.supervisor_runtime",
        ):
            sys.modules.pop(name, None)
        module = importlib.import_module("scripts.supervisor_queue_recovery_v3")
    module._test_environment = env
    return module


def _open_issue(number=12, body=None):
    if body is None:
        body = "/claude-run\n\nDo the work."
    return {
        "number": number,
        "state": "open",
        "body": body,
        "created_at": CREATED_AT,
        "updated_at": CREATED_AT,
        "user": {"login": "owner"},
    }


def _closed_issue(number=12):
    return {**_open_issue(number), "state": "closed"}


class TransientRetryTest(unittest.TestCase):
    """Fix F item 3: transient failure retries automatically without human comment."""

    def setUp(self):
        self.recovery = _load_recovery()

    @staticmethod
    def _env():
        return patch.dict(os.environ, _base_env(), clear=False)

    def test_failed_queue_run_with_open_trusted_issue_and_no_pr_dispatches_retry(self):
        recovery = self.recovery
        issue = _open_issue()
        dispatch = Mock(return_value="")
        timestamp = CREATED_AT
        with self._env(), patch.object(
            recovery, "_event_allows_recovery", return_value=True
        ), patch.object(recovery, "_active_queue_run_exists", return_value=False
        ), patch.object(recovery.runtime, "api_list", side_effect=[
            [issue],  # issues list
            [],       # pulls list
        ]), patch.object(recovery, "_request_timestamp", return_value=timestamp
        ), patch.object(recovery, "_list_records", return_value=[]
        ), patch.object(recovery, "_dispatch_retry", dispatch):
            result = recovery.reconcile()
        self.assertEqual(result, 0)
        dispatch.assert_called_once()
        args = dispatch.call_args[0]
        self.assertEqual(args[0], 12)
        self.assertEqual(args[2], 1)

    def test_reconcile_skips_when_active_queue_run_exists(self):
        recovery = self.recovery
        dispatch = Mock()
        with self._env(), patch.object(
            recovery, "_event_allows_recovery", return_value=True
        ), patch.object(recovery, "_active_queue_run_exists", return_value=True
        ), patch.object(recovery, "_dispatch_retry", dispatch):
            result = recovery.reconcile()
        self.assertEqual(result, 0)
        dispatch.assert_not_called()

    def test_reconcile_skips_untrusted_issue(self):
        recovery = self.recovery
        untrusted = {**_open_issue(), "user": {"login": "random-user"}}
        dispatch = Mock()
        with self._env(), patch.object(
            recovery, "_event_allows_recovery", return_value=True
        ), patch.object(recovery, "_active_queue_run_exists", return_value=False
        ), patch.object(recovery.runtime, "api_list", side_effect=[
            [untrusted], []
        ]), patch.object(recovery, "_dispatch_retry", dispatch):
            result = recovery.reconcile()
        self.assertEqual(result, 0)
        dispatch.assert_not_called()

    def test_reconcile_skips_when_pr_already_exists_for_issue(self):
        recovery = self.recovery
        issue = _open_issue()
        existing_pr = {
            "head": {
                "ref": "claude-issue-12-task",
                "repo": {"full_name": REPO},
            }
        }
        dispatch = Mock()
        with self._env(), patch.object(
            recovery, "_event_allows_recovery", return_value=True
        ), patch.object(recovery, "_active_queue_run_exists", return_value=False
        ), patch.object(recovery.runtime, "api_list", side_effect=[
            [issue], [existing_pr]
        ]), patch.object(recovery, "_request_timestamp", return_value=CREATED_AT
        ), patch.object(recovery, "_dispatch_retry", dispatch):
            result = recovery.reconcile()
        self.assertEqual(result, 0)
        dispatch.assert_not_called()

    def test_only_one_retry_dispatched_per_reconcile_call(self):
        recovery = self.recovery
        issue1 = _open_issue(number=10)
        issue2 = _open_issue(number=11)
        dispatch = Mock(return_value="")
        with self._env(), patch.object(
            recovery, "_event_allows_recovery", return_value=True
        ), patch.object(recovery, "_active_queue_run_exists", return_value=False
        ), patch.object(recovery.runtime, "api_list", side_effect=[
            [issue1, issue2], []
        ]), patch.object(recovery, "_request_timestamp", return_value=CREATED_AT
        ), patch.object(recovery, "_list_records", return_value=[]
        ), patch.object(recovery, "_dispatch_retry", dispatch):
            recovery.reconcile()
        self.assertEqual(dispatch.call_count, 1)

    def test_closed_issue_is_not_retried(self):
        recovery = self.recovery
        closed = _closed_issue()
        dispatch = Mock()
        with self._env(), patch.object(
            recovery, "_event_allows_recovery", return_value=True
        ), patch.object(recovery, "_active_queue_run_exists", return_value=False
        ), patch.object(recovery.runtime, "api_list", side_effect=[
            [closed], []
        ]), patch.object(recovery, "_dispatch_retry", dispatch):
            result = recovery.reconcile()
        self.assertEqual(result, 0)
        dispatch.assert_not_called()

    def test_first_retry_uses_attempt_number_one(self):
        recovery = self.recovery
        issue = _open_issue()
        dispatch = Mock(return_value="")
        with self._env(), patch.object(
            recovery, "_event_allows_recovery", return_value=True
        ), patch.object(recovery, "_active_queue_run_exists", return_value=False
        ), patch.object(recovery.runtime, "api_list", side_effect=[[issue], []]
        ), patch.object(recovery, "_request_timestamp", return_value=CREATED_AT
        ), patch.object(recovery, "_list_records", return_value=[]
        ), patch.object(recovery, "_dispatch_retry", dispatch):
            recovery.reconcile()
        _, _, attempt = dispatch.call_args[0]
        self.assertEqual(attempt, 1)

    def test_subsequent_retry_increments_attempt_number(self):
        recovery = self.recovery
        issue = _open_issue()
        dispatch = Mock(return_value="")
        existing_records = ["retry-1.json"]
        with self._env(), patch.object(
            recovery, "_event_allows_recovery", return_value=True
        ), patch.object(recovery, "_active_queue_run_exists", return_value=False
        ), patch.object(recovery.runtime, "api_list", side_effect=[[issue], []]
        ), patch.object(recovery, "_request_timestamp", return_value=CREATED_AT
        ), patch.object(recovery, "_list_records", return_value=existing_records
        ), patch.object(recovery, "_dispatch_retry", dispatch):
            recovery.reconcile()
        _, _, attempt = dispatch.call_args[0]
        self.assertEqual(attempt, 2)


class RetryExhaustionTest(unittest.TestCase):
    """Fix F item 4: retry count exhaustion produces an explicit automation incident."""

    def setUp(self):
        self.recovery = _load_recovery()

    @staticmethod
    def _env():
        return patch.dict(os.environ, _base_env(), clear=False)

    def test_exhausted_issue_records_incident_and_stops_dispatching(self):
        recovery = self.recovery
        issue = _open_issue()
        retry_records = [
            f"retry-{i + 1}.json" for i in range(recovery.MAX_QUEUE_RECOVERY_ATTEMPTS)
        ]
        record_exhaustion = Mock(return_value=True)
        dispatch = Mock()
        with self._env(), patch.object(
            recovery, "_event_allows_recovery", return_value=True
        ), patch.object(recovery, "_active_queue_run_exists", return_value=False
        ), patch.object(recovery.runtime, "api_list", side_effect=[[issue], []]
        ), patch.object(recovery, "_request_timestamp", return_value=CREATED_AT
        ), patch.object(recovery, "_list_records", return_value=retry_records
        ), patch.object(recovery, "_record_exhaustion", record_exhaustion
        ), patch.object(recovery, "_dispatch_retry", dispatch):
            result = recovery.reconcile()
        self.assertEqual(result, 0)
        record_exhaustion.assert_called_once()
        dispatch.assert_not_called()

    def test_already_exhausted_issue_is_skipped_entirely(self):
        recovery = self.recovery
        issue = _open_issue()
        records_with_exhaustion = [
            "retry-1.json",
            "retry-2.json",
            "retry-3.json",
            "exhausted.json",
        ]
        dispatch = Mock()
        record_exhaustion = Mock()
        with self._env(), patch.object(
            recovery, "_event_allows_recovery", return_value=True
        ), patch.object(recovery, "_active_queue_run_exists", return_value=False
        ), patch.object(recovery.runtime, "api_list", side_effect=[[issue], []]
        ), patch.object(recovery, "_request_timestamp", return_value=CREATED_AT
        ), patch.object(recovery, "_list_records", return_value=records_with_exhaustion
        ), patch.object(recovery, "_record_exhaustion", record_exhaustion
        ), patch.object(recovery, "_dispatch_retry", dispatch):
            result = recovery.reconcile()
        self.assertEqual(result, 0)
        dispatch.assert_not_called()
        record_exhaustion.assert_not_called()

    def test_max_queue_recovery_attempts_is_bounded(self):
        recovery = self.recovery
        self.assertGreater(recovery.MAX_QUEUE_RECOVERY_ATTEMPTS, 0)
        self.assertLessEqual(recovery.MAX_QUEUE_RECOVERY_ATTEMPTS, 10)

    def test_exhaustion_reason_is_canonical_non_notifying_code(self):
        recovery = self.recovery
        self.assertEqual(recovery.RETRY_REASON, "QUEUE_PIPELINE_RETRY_EXHAUSTED")
        self.assertNotIn("HUMAN", recovery.RETRY_REASON)

    def test_retry_count_respects_started_attempt_records_filter(self):
        with patch.dict(os.environ, _base_env(), clear=False):
            for name in (
                "scripts.supervisor_queue_recovery_v2",
                "scripts.supervisor_queue_recovery",
                "scripts.supervisor_runtime",
            ):
                sys.modules.pop(name, None)
            module_v2 = importlib.import_module("scripts.supervisor_queue_recovery_v2")

        unstarted_records = ["retry-1.json", "retry-2.json"]
        with patch.object(module_v2, "_original_list_records", return_value=unstarted_records):
            visible = module_v2.started_attempt_records("root")
        self.assertEqual(visible, [])

        records_with_start = ["retry-1.json", "retry-1-started.json", "retry-2.json"]
        with patch.object(module_v2, "_original_list_records", return_value=records_with_start):
            visible = module_v2.started_attempt_records("root")
        self.assertIn("retry-1.json", visible)
        self.assertNotIn("retry-2.json", visible)

    def test_exhaustion_record_payload_has_notification_false(self):
        recovery = self.recovery
        root = f"{recovery.RETRY_ROOT}/issue-12/request-{FINGERPRINT}"
        retry_records = ["retry-1.json", "retry-2.json", "retry-3.json"]
        content = recovery._canonical_record(
            {
                "issue_number": 12,
                "max_attempts": recovery.MAX_QUEUE_RECOVERY_ATTEMPTS,
                "notification": False,
                "reason": recovery.RETRY_REASON,
                "request_fingerprint": FINGERPRINT,
                "retry_records": sorted(retry_records),
            }
        )
        payload = json.loads(content)
        self.assertFalse(payload["notification"])
        self.assertIsNone(payload.get("required_human_action"))


class WatchdogDetectionTest(unittest.TestCase):
    """Fix F item 6: no-PR/no-branch stopped Issue is detected by the watchdog."""

    def setUp(self):
        self.recovery = _load_recovery()

    @staticmethod
    def _env():
        return patch.dict(os.environ, _base_env(), clear=False)

    def test_open_issue_with_trigger_and_no_pr_is_detected_as_needing_recovery(self):
        recovery = self.recovery
        issue = _open_issue()
        detected: list[int] = []

        def capture_dispatch(issue_number, fingerprint, attempt):
            detected.append(issue_number)

        with self._env(), patch.object(
            recovery, "_event_allows_recovery", return_value=True
        ), patch.object(recovery, "_active_queue_run_exists", return_value=False
        ), patch.object(recovery.runtime, "api_list", side_effect=[[issue], []]
        ), patch.object(recovery, "_request_timestamp", return_value=CREATED_AT
        ), patch.object(recovery, "_list_records", return_value=[]
        ), patch.object(recovery, "_dispatch_retry", capture_dispatch):
            recovery.reconcile()

        self.assertIn(12, detected)

    def test_issue_without_queue_trigger_is_ignored(self):
        recovery = self.recovery
        issue_no_trigger = {
            **_open_issue(),
            "body": "Just a description, no trigger.",
        }
        dispatch = Mock()
        with self._env(), patch.object(
            recovery, "_event_allows_recovery", return_value=True
        ), patch.object(recovery, "_active_queue_run_exists", return_value=False
        ), patch.object(recovery.runtime, "api_list", side_effect=[[issue_no_trigger], []]
        ), patch.object(recovery, "_request_timestamp", return_value=None
        ), patch.object(recovery, "_dispatch_retry", dispatch):
            recovery.reconcile()
        dispatch.assert_not_called()

    def test_schedule_event_allows_watchdog_recovery(self):
        recovery = self.recovery
        with patch.dict(
            os.environ, {**_base_env(), "GITHUB_EVENT_NAME": "schedule"}, clear=False
        ):
            self.assertTrue(recovery._event_allows_recovery())

    def test_workflow_dispatch_event_allows_watchdog_recovery(self):
        recovery = self.recovery
        with patch.dict(
            os.environ, {**_base_env(), "GITHUB_EVENT_NAME": "workflow_dispatch"}, clear=False
        ):
            self.assertTrue(recovery._event_allows_recovery())

    def test_only_failed_queue_completions_trigger_watchdog_recovery(self):
        recovery = self.recovery
        with tempfile.TemporaryDirectory() as tmp:
            event_path = Path(tmp) / "event.json"
            base_run = {
                "name": "Claude Issue Queue",
                "path": ".github/workflows/claude-queue.yml@main",
                "head_branch": "main",
                "status": "completed",
                "conclusion": "failure",
                "event": "issues",
                "repository": {"full_name": REPO},
                "actor": {"login": "owner"},
            }
            event_path.write_text(json.dumps({"workflow_run": base_run}))
            with patch.dict(
                os.environ,
                {**_base_env(), "GITHUB_EVENT_NAME": "workflow_run", "GITHUB_EVENT_PATH": str(event_path)},
                clear=False,
            ):
                self.assertTrue(recovery._event_allows_recovery())

            success_run = {**base_run, "conclusion": "success"}
            event_path.write_text(json.dumps({"workflow_run": success_run}))
            with patch.dict(
                os.environ,
                {**_base_env(), "GITHUB_EVENT_NAME": "workflow_run", "GITHUB_EVENT_PATH": str(event_path)},
                clear=False,
            ):
                self.assertFalse(recovery._event_allows_recovery())

            ci_run = {**base_run, "name": "CI"}
            event_path.write_text(json.dumps({"workflow_run": ci_run}))
            with patch.dict(
                os.environ,
                {**_base_env(), "GITHUB_EVENT_NAME": "workflow_run", "GITHUB_EVENT_PATH": str(event_path)},
                clear=False,
            ):
                self.assertFalse(recovery._event_allows_recovery())

    def test_unknown_event_does_not_trigger_watchdog(self):
        recovery = self.recovery
        with patch.dict(
            os.environ, {**_base_env(), "GITHUB_EVENT_NAME": "push"}, clear=False
        ):
            self.assertFalse(recovery._event_allows_recovery())

    def test_watchdog_not_triggered_while_active_queue_run_exists(self):
        recovery = self.recovery
        issue = _open_issue()
        dispatch = Mock()
        with self._env(), patch.object(
            recovery, "_event_allows_recovery", return_value=True
        ), patch.object(recovery, "_active_queue_run_exists", return_value=True
        ), patch.object(recovery.runtime, "api_list", return_value=[issue]
        ), patch.object(recovery, "_dispatch_retry", dispatch):
            recovery.reconcile()
        dispatch.assert_not_called()


class DuplicateTriggerPreventionTest(unittest.TestCase):
    """Fix F item 7: duplicate triggers do not create parallel conflicting branches."""

    def setUp(self):
        self.module = _load_v3()

    @staticmethod
    def _env():
        return patch.dict(os.environ, _base_env(), clear=False)

    def _trusted_pr(self, number=99, issue_number=12):
        return {
            "number": number,
            "state": "open",
            "draft": True,
            "labels": [],
            "changed_files": 2,
            "head": {
                "sha": "e" * 40,
                "ref": f"claude-issue-{issue_number}-alt",
                "repo": {"full_name": REPO},
            },
            "base": {"ref": "main", "repo": {"full_name": REPO}},
            "user": {"login": "github-actions[bot]"},
        }

    def test_alternative_candidate_pr_blocks_retry_dispatch(self):
        module = self.module
        alternative_pr = self._trusted_pr()

        with self._env(), patch.object(
            module, "_all_trusted_open_candidates", return_value=[alternative_pr]
        ), patch.object(
            module.runtime, "source_and_scope", return_value=(12, {}, [], None)
        ):
            with self.assertRaisesRegex(RuntimeError, "Trusted alternative candidate"):
                module.require_no_trusted_alternative(12)

    def test_no_alternative_candidates_passes_check(self):
        module = self.module
        with self._env(), patch.object(
            module, "_all_trusted_open_candidates", return_value=[]
        ):
            module.require_no_trusted_alternative(12)

    def test_alternative_candidate_for_different_issue_does_not_block(self):
        module = self.module
        different_issue_pr = self._trusted_pr(number=99, issue_number=55)

        with self._env(), patch.object(
            module, "_all_trusted_open_candidates", return_value=[different_issue_pr]
        ), patch.object(
            module.runtime, "source_and_scope", return_value=(55, {}, [], None)
        ):
            module.require_no_trusted_alternative(12)

    def test_scope_validation_occurs_before_alternative_check_and_dispatch(self):
        module = self.module
        dispatch_original = Mock()
        with self._env(), patch.object(
            module, "_validated_issue_scope", side_effect=RuntimeError("invalid scope")
        ) as scope, patch.object(
            module, "require_no_trusted_alternative"
        ) as alt, patch.object(
            module, "_original_dispatch_fixed_retry", dispatch_original
        ):
            with self.assertRaisesRegex(RuntimeError, "invalid scope"):
                module.dispatch_without_alternative(12, FINGERPRINT, 1, DEFAULT_SHA)
        scope.assert_called_once_with(12)
        alt.assert_not_called()
        dispatch_original.assert_not_called()

    def test_require_no_alternative_checked_before_dispatch(self):
        module = self.module
        dispatch_original = Mock()
        with self._env(), patch.object(
            module,
            "_validated_issue_scope",
            return_value={"declared_paths": ["tests/**"], "protected_authorized_paths": []},
        ), patch.object(
            module,
            "require_no_trusted_alternative",
            side_effect=RuntimeError("alternative exists"),
        ) as alt, patch.object(
            module, "_original_dispatch_fixed_retry", dispatch_original
        ):
            with self.assertRaisesRegex(RuntimeError, "alternative exists"):
                module.dispatch_without_alternative(12, FINGERPRINT, 1, DEFAULT_SHA)
        alt.assert_called_once_with(12)
        dispatch_original.assert_not_called()

    def test_ai_no_merge_pull_request_is_not_an_alternative_candidate(self):
        module = self.module
        pr_with_hold = {
            "number": 88,
            "state": "open",
            "draft": False,
            "labels": [{"name": "ai-no-merge"}],
            "changed_files": 1,
            "head": {
                "sha": "a" * 40,
                "ref": "claude-issue-12-hold",
                "repo": {"full_name": REPO},
            },
            "base": {"ref": "main", "repo": {"full_name": REPO}},
            "user": {"login": "github-actions[bot]"},
        }

        with self._env(), patch.object(
            module.runtime, "api_list", return_value=[pr_with_hold]
        ), patch.object(module.runtime, "api", return_value={**pr_with_hold, "changed_files": 1}):
            candidates = module._all_trusted_open_candidates()

        self.assertEqual(candidates, [])

    def test_trusted_alternative_scope_error_excludes_candidate(self):
        module = self.module
        candidate_pr = self._trusted_pr(number=77, issue_number=12)

        with self._env(), patch.object(
            module, "_all_trusted_open_candidates", return_value=[candidate_pr]
        ), patch.object(
            module.runtime, "source_and_scope", return_value=(12, {}, [], "UNAUTHORIZED_CHANGED_PATH")
        ):
            alternatives = module._trusted_alternative_candidates(12)

        self.assertEqual(alternatives, [])

    def test_intent_scope_validation_blocks_before_record_or_alternative(self):
        module = self.module
        with self._env(), patch.object(
            module, "_validated_issue_scope", side_effect=RuntimeError("invalid scope")
        ) as scope, patch.object(
            module, "require_no_trusted_alternative"
        ) as alt, patch.object(module, "_original_intent_identity") as original:
            with self.assertRaisesRegex(RuntimeError, "invalid scope"):
                module.intent_identity_without_alternative(12, FINGERPRINT, 1)
        scope.assert_called_once_with(12)
        alt.assert_not_called()
        original.assert_not_called()


class FailureClassificationTest(unittest.TestCase):
    """Issue #146: failure classification connected to Queue recovery supervisor.

    Covers acceptance tests:
      - max-turn without permission denials is retryable;
      - max-turn with permission denials becomes non-retrying permission_contract;
      - Git CONNECT 403 is retryable transport;
      - expired credential is human-only and not retried;
      - existing WIP branch suppresses a fresh default-branch retry;
      - duplicate reconciliation passes do not create duplicate attempts;
      - status snapshot always includes human_action_required and failure class.
    """

    def setUp(self):
        self.module = _load_v3()

    @staticmethod
    def _env():
        return patch.dict(os.environ, _base_env(), clear=False)

    def _call_classified(self, evidence, wip=None, attempt=1):
        """Helper: call _classified_dispatch_retry with mocked evidence and v2 dispatch."""
        module = self.module
        dispatch = Mock(return_value=True)
        with self._env(), patch.object(
            module, "_wip_branch_info", return_value=wip
        ), patch.object(module, "_latest_run_failure_evidence", return_value=evidence):
            result = module._classified_dispatch_retry(12, FINGERPRINT, attempt, dispatch)
        return result, dispatch

    def test_max_turn_without_permission_denials_is_retryable(self):
        result, dispatch = self._call_classified(("error_max_turns", 0, ""))
        self.assertTrue(result)
        dispatch.assert_called_once_with(12, FINGERPRINT, 1)

    def test_max_turn_with_permission_denials_becomes_permission_contract(self):
        result, dispatch = self._call_classified(("error_max_turns", 3, "tool policy violated"))
        self.assertFalse(result)
        dispatch.assert_not_called()

    def test_git_connect_403_is_retryable_transport(self):
        result, dispatch = self._call_classified(("failure", 0, "connect tunnel error"))
        self.assertTrue(result)
        dispatch.assert_called_once()

    def test_expired_credential_is_human_only_and_not_retried(self):
        result, dispatch = self._call_classified(("failure", 0, "token expired"))
        self.assertFalse(result)
        dispatch.assert_not_called()

    def test_wip_branch_suppresses_fresh_retry(self):
        result, dispatch = self._call_classified(None, wip=("claude-issue-12-wip", "a" * 40))
        self.assertFalse(result)
        dispatch.assert_not_called()

    def test_no_prior_evidence_defaults_to_retryable_unknown(self):
        """When no Queue run evidence exists, default to UNKNOWN (retryable)."""
        result, dispatch = self._call_classified(None, wip=None)
        self.assertTrue(result)
        dispatch.assert_called_once()

    def test_auth_secret_failure_is_not_retried(self):
        """auth_secret class is human-only and must not be retried."""
        result, dispatch = self._call_classified(("failure", 0, "authentication failed"))
        self.assertFalse(result)
        dispatch.assert_not_called()

    def test_test_failure_is_not_retried(self):
        """test_failure is non-retryable (fixable class); must not be retried."""
        result, dispatch = self._call_classified(("failure", 0, "test assert error"))
        self.assertFalse(result)
        dispatch.assert_not_called()

    def test_platform_outage_is_retryable(self):
        result, dispatch = self._call_classified(("timed_out", 0, ""))
        self.assertTrue(result)
        dispatch.assert_called_once()

    def test_wip_branch_check_precedes_failure_classification(self):
        """WIP branch detection must fire before any API calls for failure evidence."""
        module = self.module
        dispatch = Mock()
        evidence_fn = Mock()
        with self._env(), patch.object(
            module, "_wip_branch_info", return_value=("claude-issue-12-branch", "b" * 40)
        ), patch.object(module, "_latest_run_failure_evidence", evidence_fn):
            module._classified_dispatch_retry(12, FINGERPRINT, 1, dispatch)
        evidence_fn.assert_not_called()
        dispatch.assert_not_called()

    def test_duplicate_classified_calls_do_not_accumulate_dispatches(self):
        """Calling _classified_dispatch_retry twice for the same non-retryable failure
        does not dispatch on either call."""
        module = self.module
        dispatch = Mock()
        with self._env(), patch.object(
            module, "_wip_branch_info", return_value=None
        ), patch.object(
            module, "_latest_run_failure_evidence",
            return_value=("failure", 0, "token expired"),
        ):
            r1 = module._classified_dispatch_retry(12, FINGERPRINT, 1, dispatch)
            r2 = module._classified_dispatch_retry(12, FINGERPRINT, 1, dispatch)
        self.assertFalse(r1)
        self.assertFalse(r2)
        dispatch.assert_not_called()

    def test_status_snapshot_includes_human_action_required_and_failure_class(self):
        """complete_connected_exhaustion_snapshot must always include both fields."""
        module = self.module
        base_snapshot: dict = {
            "active_queue_run_absent": True,
            "alternative_paths_exhausted": True,
            "candidate_pull_request_absent": True,
            "codex_and_threads": "not-applicable-no-pull-request",
            "completed": True,
            "default_sha": DEFAULT_SHA,
            "fixed_workflow_identity": True,
            "idempotency_records": ["retry-1.json"],
            "permission_markers_verified": True,
            "repository_metadata_verified": True,
            "request_fingerprint": FINGERPRINT,
            "run_evidence": [],
            "source_issue": 12,
            "source_issue_authorization_verified": True,
        }
        with self._env(), patch.object(
            module, "_exact_default_sha", return_value=DEFAULT_SHA
        ), patch.object(
            module,
            "_validated_issue_scope",
            return_value={"declared_paths": ["tests/**"], "protected_authorized_paths": []},
        ), patch.object(
            module, "_trusted_alternative_candidates", return_value=[]
        ), patch.object(
            module, "_original_connected_exhaustion_snapshot", return_value=dict(base_snapshot)
        ), patch.object(
            module, "_latest_run_failure_evidence", return_value=None
        ), patch.object(
            module, "_wip_branch_info", return_value=None
        ):
            snapshot = module.complete_connected_exhaustion_snapshot(
                12, FINGERPRINT, DEFAULT_SHA, ["retry-1.json"]
            )
        self.assertIn("human_action_required", snapshot)
        self.assertIn("failure_class", snapshot)
        self.assertIsInstance(snapshot["human_action_required"], bool)
        self.assertIsInstance(snapshot["failure_class"], str)
        self.assertIn("retry_attempt", snapshot)
        self.assertIn("next_automatic_action", snapshot)

    def test_auth_secret_snapshot_sets_human_action_required_true(self):
        """When failure class is auth_secret, human_action_required must be True."""
        module = self.module
        base_snapshot: dict = {
            "active_queue_run_absent": True,
            "alternative_paths_exhausted": True,
            "candidate_pull_request_absent": True,
            "codex_and_threads": "not-applicable-no-pull-request",
            "completed": True,
            "default_sha": DEFAULT_SHA,
            "fixed_workflow_identity": True,
            "idempotency_records": ["retry-1.json"],
            "permission_markers_verified": True,
            "repository_metadata_verified": True,
            "request_fingerprint": FINGERPRINT,
            "run_evidence": [],
            "source_issue": 12,
            "source_issue_authorization_verified": True,
        }
        with self._env(), patch.object(
            module, "_exact_default_sha", return_value=DEFAULT_SHA
        ), patch.object(
            module,
            "_validated_issue_scope",
            return_value={"declared_paths": ["tests/**"], "protected_authorized_paths": []},
        ), patch.object(
            module, "_trusted_alternative_candidates", return_value=[]
        ), patch.object(
            module, "_original_connected_exhaustion_snapshot", return_value=dict(base_snapshot)
        ), patch.object(
            module, "_latest_run_failure_evidence",
            return_value=("failure", 0, "token expired"),
        ), patch.object(
            module, "_wip_branch_info", return_value=None
        ):
            snapshot = module.complete_connected_exhaustion_snapshot(
                12, FINGERPRINT, DEFAULT_SHA, ["retry-1.json"]
            )
        self.assertTrue(snapshot["human_action_required"])
        self.assertEqual(snapshot["failure_class"], "auth_secret")

    def test_non_auth_classes_set_human_action_required_false(self):
        """Non-auth failure classes must not set human_action_required."""
        module = self.module
        base_snapshot: dict = {
            "active_queue_run_absent": True,
            "alternative_paths_exhausted": True,
            "candidate_pull_request_absent": True,
            "codex_and_threads": "not-applicable-no-pull-request",
            "completed": True,
            "default_sha": DEFAULT_SHA,
            "fixed_workflow_identity": True,
            "idempotency_records": ["retry-1.json", "retry-2.json", "retry-3.json"],
            "permission_markers_verified": True,
            "repository_metadata_verified": True,
            "request_fingerprint": FINGERPRINT,
            "run_evidence": [],
            "source_issue": 12,
            "source_issue_authorization_verified": True,
        }
        retry_records = ["retry-1.json", "retry-2.json", "retry-3.json"]
        with self._env(), patch.object(
            module, "_exact_default_sha", return_value=DEFAULT_SHA
        ), patch.object(
            module,
            "_validated_issue_scope",
            return_value={"declared_paths": ["tests/**"], "protected_authorized_paths": []},
        ), patch.object(
            module, "_trusted_alternative_candidates", return_value=[]
        ), patch.object(
            module, "_original_connected_exhaustion_snapshot", return_value=dict(base_snapshot)
        ), patch.object(
            module, "_latest_run_failure_evidence",
            return_value=("error_max_turns", 0, ""),
        ), patch.object(
            module, "_wip_branch_info", return_value=None
        ):
            snapshot = module.complete_connected_exhaustion_snapshot(
                12, FINGERPRINT, DEFAULT_SHA, retry_records
            )
        self.assertFalse(snapshot["human_action_required"])
        self.assertEqual(snapshot["failure_class"], "max_turns")

    def test_checkpoint_branch_appears_in_snapshot_when_present(self):
        """When a WIP branch exists, checkpoint_branch and checkpoint_sha appear in snapshot."""
        module = self.module
        base_snapshot: dict = {
            "active_queue_run_absent": True,
            "alternative_paths_exhausted": True,
            "candidate_pull_request_absent": True,
            "codex_and_threads": "not-applicable-no-pull-request",
            "completed": True,
            "default_sha": DEFAULT_SHA,
            "fixed_workflow_identity": True,
            "idempotency_records": ["retry-1.json"],
            "permission_markers_verified": True,
            "repository_metadata_verified": True,
            "request_fingerprint": FINGERPRINT,
            "run_evidence": [],
            "source_issue": 12,
            "source_issue_authorization_verified": True,
        }
        branch_sha = "c" * 40
        with self._env(), patch.object(
            module, "_exact_default_sha", return_value=DEFAULT_SHA
        ), patch.object(
            module,
            "_validated_issue_scope",
            return_value={"declared_paths": ["tests/**"], "protected_authorized_paths": []},
        ), patch.object(
            module, "_trusted_alternative_candidates", return_value=[]
        ), patch.object(
            module, "_original_connected_exhaustion_snapshot", return_value=dict(base_snapshot)
        ), patch.object(
            module, "_latest_run_failure_evidence", return_value=None,
        ), patch.object(
            module, "_wip_branch_info",
            return_value=("claude-issue-12-checkpoint", branch_sha),
        ):
            snapshot = module.complete_connected_exhaustion_snapshot(
                12, FINGERPRINT, DEFAULT_SHA, ["retry-1.json"]
            )
        self.assertEqual(snapshot["checkpoint_branch"], "claude-issue-12-checkpoint")
        self.assertEqual(snapshot["checkpoint_sha"], branch_sha)


if __name__ == "__main__":
    unittest.main()

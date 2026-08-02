"""Tests for queue_failure_classifier.

Covers Fix F items 1, 2, and 5:
  1. max-turn failure is classified as automation-owned and triggers auto-retry;
  2. validation required but disallowed is rejected before model execution;
  5. human-only auth/Secret failure is not retried automatically.
"""
import unittest

from scripts.queue_failure_classifier import (
    FailureClass,
    FailureStatus,
    HUMAN_ONLY_CLASSES,
    RETRYABLE_CLASSES,
    build_failure_status,
    check_tool_permission_contract,
    classify_conclusion,
    is_human_only_failure,
    should_auto_retry,
)


class FailureClassificationTest(unittest.TestCase):
    # ------------------------------------------------------------------
    # Fix F item 1: max-turn failure is automation-owned and auto-retried
    # ------------------------------------------------------------------

    def test_error_max_turns_classifies_as_max_turns(self):
        result = classify_conclusion("error_max_turns")
        self.assertEqual(result, FailureClass.MAX_TURNS)

    def test_max_turns_is_not_human_only(self):
        self.assertFalse(is_human_only_failure(FailureClass.MAX_TURNS))

    def test_max_turns_is_retryable_within_budget(self):
        self.assertTrue(should_auto_retry(FailureClass.MAX_TURNS, 0, 3))
        self.assertTrue(should_auto_retry(FailureClass.MAX_TURNS, 2, 3))

    def test_max_turns_retry_stops_at_budget_exhaustion(self):
        self.assertFalse(should_auto_retry(FailureClass.MAX_TURNS, 3, 3))
        self.assertFalse(should_auto_retry(FailureClass.MAX_TURNS, 4, 3))

    def test_max_turns_status_has_human_action_required_false(self):
        status = build_failure_status(FailureClass.MAX_TURNS, retry_attempt=0, max_retries=3)
        self.assertFalse(status.human_action_required)
        self.assertIn("automatic retry", status.next_automatic_action)

    def test_max_turns_exhaustion_status_records_incident_without_human_flag(self):
        status = build_failure_status(FailureClass.MAX_TURNS, retry_attempt=3, max_retries=3)
        self.assertFalse(status.human_action_required)
        self.assertIn("exhausted", status.next_automatic_action)

    def test_max_turns_status_comment_contains_human_action_required_false(self):
        status = build_failure_status(FailureClass.MAX_TURNS, retry_attempt=1, max_retries=3)
        comment = status.as_status_comment()
        self.assertIn("human_action_required: `false`", comment)
        self.assertIn("failure_class: `max_turns`", comment)
        self.assertIn("retry_attempt: `1`", comment)

    def test_max_turns_status_includes_checkpoint_when_provided(self):
        sha = "a" * 40
        status = build_failure_status(
            FailureClass.MAX_TURNS,
            retry_attempt=1,
            max_retries=3,
            checkpoint_sha=sha,
            checkpoint_artifact="wip-patch.diff",
        )
        comment = status.as_status_comment()
        self.assertIn(f"checkpoint_sha: `{sha}`", comment)
        self.assertIn("checkpoint_artifact: `wip-patch.diff`", comment)

    # ------------------------------------------------------------------
    # Fix F item 2: validation required but disallowed is caught preflight
    # ------------------------------------------------------------------

    def test_required_validation_command_not_in_allowed_tools_is_denied(self):
        denied = check_tool_permission_contract(
            required_commands=["python", "pytest"],
            allowed_bash_commands=["git add", "git commit", "git push"],
        )
        self.assertIn("python", denied)
        self.assertIn("pytest", denied)

    def test_all_required_commands_allowed_returns_empty_denial_list(self):
        denied = check_tool_permission_contract(
            required_commands=["git"],
            allowed_bash_commands=["git add", "git commit", "git push", "git rm"],
        )
        self.assertEqual(denied, [])

    def test_partial_allowlist_returns_only_unallowed_commands(self):
        denied = check_tool_permission_contract(
            required_commands=["git", "python", "pip"],
            allowed_bash_commands=["git add", "git commit"],
        )
        self.assertNotIn("git", denied)
        self.assertIn("python", denied)
        self.assertIn("pip", denied)

    def test_empty_required_commands_returns_no_denials(self):
        denied = check_tool_permission_contract(
            required_commands=[],
            allowed_bash_commands=["git add"],
        )
        self.assertEqual(denied, [])

    def test_empty_allowed_commands_denies_all_required(self):
        required = ["python", "pytest", "pip install"]
        denied = check_tool_permission_contract(
            required_commands=required,
            allowed_bash_commands=[],
        )
        self.assertEqual(denied, required)

    def test_case_insensitive_matching(self):
        denied = check_tool_permission_contract(
            required_commands=["Python", "PYTEST"],
            allowed_bash_commands=["python", "pytest"],
        )
        self.assertEqual(denied, [])

    def test_base_command_match_on_subcommand_spec(self):
        denied = check_tool_permission_contract(
            required_commands=["git"],
            allowed_bash_commands=["git add", "git commit", "git push"],
        )
        self.assertEqual(denied, [])

    def test_permission_denials_count_classifies_as_contract_not_transient(self):
        result = classify_conclusion("failure", permission_denials_count=6)
        self.assertEqual(result, FailureClass.PERMISSION_CONTRACT)

    def test_permission_contract_is_not_retryable(self):
        self.assertFalse(should_auto_retry(FailureClass.PERMISSION_CONTRACT, 0, 3))

    def test_permission_contract_is_not_human_only(self):
        self.assertFalse(is_human_only_failure(FailureClass.PERMISSION_CONTRACT))

    def test_permission_contract_status_shows_fix_required_not_human(self):
        status = build_failure_status(
            FailureClass.PERMISSION_CONTRACT, retry_attempt=0, max_retries=3
        )
        self.assertFalse(status.human_action_required)
        self.assertIn("fix required", status.next_automatic_action)
        comment = status.as_status_comment()
        self.assertIn("human_action_required: `false`", comment)

    # ------------------------------------------------------------------
    # Fix F item 5: human-only auth/Secret failure is never auto-retried
    # ------------------------------------------------------------------

    def test_auth_secret_failure_is_human_only(self):
        self.assertTrue(is_human_only_failure(FailureClass.AUTH_SECRET))

    def test_auth_secret_is_never_auto_retried_regardless_of_attempt_count(self):
        for attempt in range(10):
            with self.subTest(attempt=attempt):
                self.assertFalse(should_auto_retry(FailureClass.AUTH_SECRET, attempt, 3))

    def test_auth_secret_status_has_human_action_required_true(self):
        status = build_failure_status(FailureClass.AUTH_SECRET, retry_attempt=0, max_retries=3)
        self.assertTrue(status.human_action_required)
        self.assertIn("human UI action", status.next_automatic_action)

    def test_auth_secret_status_comment_contains_human_action_required_true(self):
        status = build_failure_status(FailureClass.AUTH_SECRET, retry_attempt=0, max_retries=3)
        comment = status.as_status_comment()
        self.assertIn("human_action_required: `true`", comment)

    def test_credential_error_detail_classifies_as_auth_secret(self):
        result = classify_conclusion("failure", error_detail="credential expired token")
        self.assertEqual(result, FailureClass.AUTH_SECRET)

    def test_401_in_error_detail_classifies_as_auth_secret(self):
        result = classify_conclusion("failure", error_detail="HTTP 401 Unauthorized")
        self.assertEqual(result, FailureClass.AUTH_SECRET)

    def test_expired_token_classifies_as_auth_secret(self):
        result = classify_conclusion("failure", error_detail="token expired")
        self.assertEqual(result, FailureClass.AUTH_SECRET)

    # ------------------------------------------------------------------
    # Class invariants
    # ------------------------------------------------------------------

    def test_human_only_classes_and_retryable_classes_are_disjoint(self):
        self.assertTrue(HUMAN_ONLY_CLASSES.isdisjoint(RETRYABLE_CLASSES))

    def test_all_failure_classes_have_explicit_classification(self):
        for fc in FailureClass:
            human = is_human_only_failure(fc)
            retryable = fc in RETRYABLE_CLASSES
            from scripts.queue_failure_classifier import FIXABLE_CLASSES
            fixable = fc in FIXABLE_CLASSES
            belongs = human or retryable or fixable
            self.assertTrue(belongs, f"{fc} is not in any classification set")

    def test_git_transport_is_retryable_and_not_human_only(self):
        self.assertFalse(is_human_only_failure(FailureClass.GIT_TRANSPORT))
        self.assertTrue(should_auto_retry(FailureClass.GIT_TRANSPORT, 0, 3))

    def test_platform_outage_is_retryable_and_not_human_only(self):
        self.assertFalse(is_human_only_failure(FailureClass.PLATFORM_OUTAGE))
        self.assertTrue(should_auto_retry(FailureClass.PLATFORM_OUTAGE, 0, 3))

    def test_timed_out_conclusion_classifies_as_platform_outage(self):
        result = classify_conclusion("timed_out")
        self.assertEqual(result, FailureClass.PLATFORM_OUTAGE)

    def test_git_push_error_classifies_as_git_transport(self):
        result = classify_conclusion("failure", error_detail="git push failed: transport error")
        self.assertEqual(result, FailureClass.GIT_TRANSPORT)

    def test_unknown_failure_is_retryable_and_not_human_only(self):
        result = classify_conclusion("failure")
        self.assertEqual(result, FailureClass.UNKNOWN)
        self.assertFalse(is_human_only_failure(result))
        self.assertTrue(should_auto_retry(result, 0, 3))

    def test_failure_status_is_immutable(self):
        status = build_failure_status(FailureClass.MAX_TURNS, 1, 3)
        with self.assertRaises(Exception):
            status.failure_class = FailureClass.UNKNOWN  # type: ignore[misc]

    def test_status_comment_always_contains_foundation_marker(self):
        for fc in FailureClass:
            with self.subTest(fc=fc):
                status = build_failure_status(fc, 0, 3)
                comment = status.as_status_comment()
                self.assertIn("<!-- foundation-failure-status -->", comment)


if __name__ == "__main__":
    unittest.main()

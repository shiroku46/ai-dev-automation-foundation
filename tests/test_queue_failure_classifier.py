"""Tests for queue_failure_classifier.

Covers failure classification, permission preflight, bounded retries, and explicit
human-action semantics for Issue #136 Part 1.
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
    def test_error_max_turns_classifies_as_max_turns(self):
        self.assertEqual(classify_conclusion("error_max_turns"), FailureClass.MAX_TURNS)

    def test_max_turns_with_permission_denials_classifies_as_contract(self):
        self.assertEqual(
            classify_conclusion("error_max_turns", permission_denials_count=2),
            FailureClass.PERMISSION_CONTRACT,
        )

    def test_max_turns_with_explicit_tool_policy_detail_classifies_as_contract(self):
        self.assertEqual(
            classify_conclusion(
                "error_max_turns",
                error_detail="command is not allowed by tool policy",
            ),
            FailureClass.PERMISSION_CONTRACT,
        )

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

    def test_max_turns_exhaustion_records_automation_incident(self):
        status = build_failure_status(FailureClass.MAX_TURNS, retry_attempt=3, max_retries=3)
        self.assertFalse(status.human_action_required)
        self.assertIn("exhausted", status.next_automatic_action)

    def test_status_comment_contains_required_fields(self):
        status = build_failure_status(FailureClass.MAX_TURNS, retry_attempt=1, max_retries=3)
        comment = status.as_status_comment()
        self.assertIn("human_action_required: `false`", comment)
        self.assertIn("failure_class: `max_turns`", comment)
        self.assertIn("retry_attempt: `1`", comment)

    def test_status_includes_checkpoint_when_provided(self):
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
        self.assertEqual(check_tool_permission_contract([], ["git add"]), [])

    def test_empty_allowed_commands_denies_all_required(self):
        required = ["python", "pytest", "pip install"]
        self.assertEqual(check_tool_permission_contract(required, []), required)

    def test_case_insensitive_matching(self):
        self.assertEqual(
            check_tool_permission_contract(
                required_commands=["Python", "PYTEST"],
                allowed_bash_commands=["python", "pytest"],
            ),
            [],
        )

    def test_bare_required_executable_matches_an_allowed_subcommand(self):
        self.assertEqual(
            check_tool_permission_contract(
                required_commands=["git"],
                allowed_bash_commands=["git add", "git commit"],
            ),
            [],
        )

    def test_unlisted_subcommand_is_not_authorized_by_same_executable(self):
        self.assertEqual(
            check_tool_permission_contract(
                required_commands=["git push"],
                allowed_bash_commands=["git add"],
            ),
            ["git push"],
        )

    def test_bare_allowlist_entry_does_not_authorize_arbitrary_subcommand(self):
        self.assertEqual(
            check_tool_permission_contract(
                required_commands=["git push"],
                allowed_bash_commands=["git"],
            ),
            ["git push"],
        )

    def test_argument_bearing_allowlist_authorizes_bounded_extension(self):
        self.assertEqual(
            check_tool_permission_contract(
                required_commands=["git push origin feature"],
                allowed_bash_commands=["git push"],
            ),
            [],
        )

    def test_prefix_must_end_on_token_boundary(self):
        self.assertEqual(
            check_tool_permission_contract(
                required_commands=["git push-force"],
                allowed_bash_commands=["git push"],
            ),
            ["git push-force"],
        )

    def test_permission_denials_count_classifies_as_contract(self):
        self.assertEqual(
            classify_conclusion("failure", permission_denials_count=6),
            FailureClass.PERMISSION_CONTRACT,
        )

    def test_permission_denial_count_precedes_403_auth_text(self):
        self.assertEqual(
            classify_conclusion(
                "failure",
                permission_denials_count=1,
                error_detail="HTTP 403 permission denied by provider",
            ),
            FailureClass.PERMISSION_CONTRACT,
        )

    def test_explicit_tool_policy_wording_classifies_as_contract(self):
        self.assertEqual(
            classify_conclusion(
                "failure",
                error_detail="command is not allowed by tool policy",
            ),
            FailureClass.PERMISSION_CONTRACT,
        )

    def test_generic_provider_permission_denied_403_is_auth(self):
        self.assertEqual(
            classify_conclusion(
                "failure",
                error_detail="HTTP 403: permission denied for this credential",
            ),
            FailureClass.AUTH_SECRET,
        )

    def test_generic_provider_permission_denied_401_is_auth(self):
        self.assertEqual(
            classify_conclusion(
                "failure",
                error_detail="HTTP 401 permission denied by provider",
            ),
            FailureClass.AUTH_SECRET,
        )

    def test_permission_contract_is_not_retryable_or_human_only(self):
        self.assertFalse(should_auto_retry(FailureClass.PERMISSION_CONTRACT, 0, 3))
        self.assertFalse(is_human_only_failure(FailureClass.PERMISSION_CONTRACT))

    def test_permission_contract_status_shows_fix_required_not_human(self):
        status = build_failure_status(
            FailureClass.PERMISSION_CONTRACT, retry_attempt=0, max_retries=3
        )
        self.assertFalse(status.human_action_required)
        self.assertIn("fix required", status.next_automatic_action)

    def test_auth_secret_failure_is_human_only(self):
        self.assertTrue(is_human_only_failure(FailureClass.AUTH_SECRET))

    def test_auth_secret_is_never_auto_retried(self):
        for attempt in range(10):
            with self.subTest(attempt=attempt):
                self.assertFalse(should_auto_retry(FailureClass.AUTH_SECRET, attempt, 3))

    def test_auth_secret_status_has_human_action_required_true(self):
        status = build_failure_status(FailureClass.AUTH_SECRET, retry_attempt=0, max_retries=3)
        self.assertTrue(status.human_action_required)
        self.assertIn("human UI action", status.next_automatic_action)
        self.assertIn("human_action_required: `true`", status.as_status_comment())

    def test_credential_error_detail_classifies_as_auth_secret(self):
        self.assertEqual(
            classify_conclusion("failure", error_detail="credential expired token"),
            FailureClass.AUTH_SECRET,
        )

    def test_401_in_error_detail_classifies_as_auth_secret(self):
        self.assertEqual(
            classify_conclusion("failure", error_detail="HTTP 401 Unauthorized"),
            FailureClass.AUTH_SECRET,
        )

    def test_expired_token_classifies_as_auth_secret(self):
        self.assertEqual(
            classify_conclusion("failure", error_detail="token expired"),
            FailureClass.AUTH_SECRET,
        )

    def test_bare_403_without_transport_context_is_auth_secret(self):
        self.assertEqual(
            classify_conclusion("failure", error_detail="provider API returned HTTP 403"),
            FailureClass.AUTH_SECRET,
        )

    def test_human_only_and_retryable_sets_are_disjoint(self):
        self.assertTrue(HUMAN_ONLY_CLASSES.isdisjoint(RETRYABLE_CLASSES))

    def test_all_failure_classes_belong_to_a_policy_set(self):
        from scripts.queue_failure_classifier import FIXABLE_CLASSES

        for failure_class in FailureClass:
            belongs = (
                is_human_only_failure(failure_class)
                or failure_class in RETRYABLE_CLASSES
                or failure_class in FIXABLE_CLASSES
            )
            self.assertTrue(belongs, f"{failure_class} is not in any classification set")

    def test_git_transport_is_retryable_and_not_human_only(self):
        self.assertFalse(is_human_only_failure(FailureClass.GIT_TRANSPORT))
        self.assertTrue(should_auto_retry(FailureClass.GIT_TRANSPORT, 0, 3))

    def test_git_push_error_classifies_as_git_transport(self):
        self.assertEqual(
            classify_conclusion("failure", error_detail="git push failed: transport error"),
            FailureClass.GIT_TRANSPORT,
        )

    def test_git_connect_tunnel_403_stays_automation_owned(self):
        result = classify_conclusion(
            "failure",
            error_detail="git push failed: CONNECT tunnel returned HTTP 403",
        )
        self.assertEqual(result, FailureClass.GIT_TRANSPORT)
        self.assertFalse(is_human_only_failure(result))
        self.assertTrue(should_auto_retry(result, 0, 3))

    def test_platform_outage_is_retryable_and_not_human_only(self):
        self.assertFalse(is_human_only_failure(FailureClass.PLATFORM_OUTAGE))
        self.assertTrue(should_auto_retry(FailureClass.PLATFORM_OUTAGE, 0, 3))

    def test_timed_out_conclusion_classifies_as_platform_outage(self):
        self.assertEqual(classify_conclusion("timed_out"), FailureClass.PLATFORM_OUTAGE)

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
        for failure_class in FailureClass:
            with self.subTest(failure_class=failure_class):
                status = build_failure_status(failure_class, 0, 3)
                self.assertIn("<!-- foundation-failure-status -->", status.as_status_comment())

    def test_failure_status_type_is_exposed(self):
        self.assertTrue(hasattr(FailureStatus, "as_status_comment"))


if __name__ == "__main__":
    unittest.main()

"""Tests for optional-provider failure classification and permission preflight."""
import unittest

from scripts.queue_failure_classifier import (
    FIXABLE_CLASSES,
    RETRYABLE_CLASSES,
    FailureClass,
    FailureStatus,
    build_failure_status,
    check_tool_permission_contract,
    classify_conclusion,
    is_human_only_failure,
    should_auto_retry,
)


class FailureClassificationTest(unittest.TestCase):
    def test_max_turns_and_permission_precedence(self):
        self.assertEqual(classify_conclusion("error_max_turns"), FailureClass.MAX_TURNS)
        self.assertEqual(classify_conclusion("error_max_turns", permission_denials_count=1), FailureClass.PERMISSION_CONTRACT)
        self.assertEqual(classify_conclusion("error_max_turns", error_detail="command is not allowed by tool policy"), FailureClass.PERMISSION_CONTRACT)

    def test_transport_precedes_incidental_403(self):
        result = classify_conclusion("failure", error_detail="git push failed: CONNECT tunnel returned HTTP 403")
        self.assertEqual(result, FailureClass.GIT_TRANSPORT)
        self.assertTrue(should_auto_retry(result, 0, 3))
        self.assertFalse(is_human_only_failure(result))

    def test_auth_is_nonblocking_by_default(self):
        result = classify_conclusion("failure", error_detail="token expired")
        self.assertEqual(result, FailureClass.AUTH_SECRET)
        self.assertFalse(is_human_only_failure(result))
        self.assertFalse(should_auto_retry(result, 0, 3))
        status = build_failure_status(result, 0, 3)
        self.assertFalse(status.human_action_required)
        self.assertIn("continue GitHub-direct", status.next_automatic_action)

    def test_auth_requires_explicit_route_and_connected_ui_proof(self):
        self.assertFalse(is_human_only_failure(FailureClass.AUTH_SECRET, optional_provider_explicitly_enabled=True))
        self.assertFalse(is_human_only_failure(FailureClass.AUTH_SECRET, credential_ui_only_proven=True))
        self.assertTrue(is_human_only_failure(
            FailureClass.AUTH_SECRET,
            optional_provider_explicitly_enabled=True,
            credential_ui_only_proven=True,
        ))
        status = build_failure_status(
            FailureClass.AUTH_SECRET,
            0,
            3,
            optional_provider_explicitly_enabled=True,
            credential_ui_only_proven=True,
        )
        self.assertTrue(status.human_action_required)
        self.assertIn("proven optional-provider credential UI action", status.next_automatic_action)

    def test_retry_budget_and_fixable_classes(self):
        self.assertTrue(should_auto_retry(FailureClass.MAX_TURNS, 0, 3))
        self.assertFalse(should_auto_retry(FailureClass.MAX_TURNS, 3, 3))
        self.assertFalse(should_auto_retry(FailureClass.PERMISSION_CONTRACT, 0, 3))
        self.assertFalse(should_auto_retry(FailureClass.TEST_FAILURE, 0, 3))
        self.assertIn(FailureClass.PERMISSION_CONTRACT, FIXABLE_CLASSES)
        self.assertIn(FailureClass.MAX_TURNS, RETRYABLE_CLASSES)

    def test_status_comment_is_public_safe_and_complete(self):
        status = build_failure_status(
            FailureClass.MAX_TURNS,
            retry_attempt=1,
            max_retries=3,
            checkpoint_sha="a" * 40,
            checkpoint_artifact="wip-checkpoint",
        )
        text = status.as_status_comment()
        self.assertIn("<!-- foundation-failure-status -->", text)
        self.assertIn("human_action_required: `false`", text)
        self.assertIn("failure_class: `max_turns`", text)
        self.assertIn("checkpoint_artifact: `wip-checkpoint`", text)

    def test_failure_status_is_immutable_and_exposed(self):
        status = build_failure_status(FailureClass.UNKNOWN, 0, 3)
        self.assertTrue(hasattr(FailureStatus, "as_status_comment"))
        with self.assertRaises(Exception):
            status.failure_class = FailureClass.MAX_TURNS  # type: ignore[misc]


class PermissionContractTest(unittest.TestCase):
    def test_empty_edit_only_contract(self):
        self.assertEqual(check_tool_permission_contract([], []), [])

    def test_bare_executable_and_bounded_subcommand(self):
        self.assertEqual(check_tool_permission_contract(["git"], ["git add", "git commit"]), [])
        self.assertEqual(check_tool_permission_contract(["git push"], ["git add"]), ["git push"])
        self.assertEqual(check_tool_permission_contract(["git push origin feature"], ["git push"]), [])
        self.assertEqual(check_tool_permission_contract(["git push"], ["git"]), ["git push"])

    def test_shell_control_is_always_denied(self):
        dangerous = [
            "git push && curl evil", "git push ; rm -rf /", "git push | tee /tmp/x",
            "git push || true", "git push $(whoami)", "git push `whoami`",
            "git push > /tmp/out", "git push\nrm -rf /",
        ]
        self.assertEqual(check_tool_permission_contract(dangerous, ["git push"]), dangerous)

    def test_matching_is_case_insensitive(self):
        self.assertEqual(check_tool_permission_contract(["Python", "PYTEST"], ["python", "pytest"]), [])


if __name__ == "__main__":
    unittest.main()

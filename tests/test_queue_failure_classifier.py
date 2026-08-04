"""Tests for optional-provider failure classification and permission preflight."""
import unittest

from scripts.queue_failure_classifier import (
    FIXABLE_CLASSES,
    RETRYABLE_CLASSES,
    FailureClass,
    build_failure_status,
    check_tool_permission_contract,
    classify_conclusion,
    is_human_only_failure,
    should_auto_retry,
)


class FailureClassificationTest(unittest.TestCase):
    def test_permission_contract_precedes_max_turns(self):
        self.assertEqual(classify_conclusion("error_max_turns"), FailureClass.MAX_TURNS)
        self.assertEqual(classify_conclusion("error_max_turns", 1), FailureClass.PERMISSION_CONTRACT)

    def test_transport_precedes_incidental_403(self):
        result = classify_conclusion("failure", error_detail="CONNECT tunnel returned HTTP 403")
        self.assertEqual(result, FailureClass.GIT_TRANSPORT)
        self.assertTrue(should_auto_retry(result, 0, 3))

    def test_auth_is_nonblocking_without_both_connected_proofs(self):
        result = classify_conclusion("failure", error_detail="token expired")
        self.assertEqual(result, FailureClass.AUTH_SECRET)
        self.assertFalse(is_human_only_failure(result))
        self.assertFalse(is_human_only_failure(result, optional_provider_explicitly_enabled=True))
        self.assertFalse(is_human_only_failure(result, credential_ui_only_proven=True))
        status = build_failure_status(result, 0, 3)
        self.assertFalse(status.human_action_required)
        self.assertIn("continue GitHub-direct", status.next_automatic_action)

    def test_auth_is_human_only_with_explicit_route_and_ui_proof(self):
        self.assertTrue(is_human_only_failure(
            FailureClass.AUTH_SECRET,
            optional_provider_explicitly_enabled=True,
            credential_ui_only_proven=True,
        ))
        status = build_failure_status(
            FailureClass.AUTH_SECRET, 0, 3,
            optional_provider_explicitly_enabled=True,
            credential_ui_only_proven=True,
        )
        self.assertTrue(status.human_action_required)

    def test_retry_and_fixable_sets(self):
        self.assertIn(FailureClass.MAX_TURNS, RETRYABLE_CLASSES)
        self.assertIn(FailureClass.PERMISSION_CONTRACT, FIXABLE_CLASSES)
        self.assertFalse(should_auto_retry(FailureClass.PERMISSION_CONTRACT, 0, 3))
        self.assertFalse(should_auto_retry(FailureClass.MAX_TURNS, 3, 3))

    def test_status_comment_is_public_safe(self):
        text = build_failure_status(
            FailureClass.MAX_TURNS, 1, 3,
            checkpoint_sha="a" * 40,
            checkpoint_artifact="wip-checkpoint",
        ).as_status_comment()
        self.assertIn("human_action_required: `false`", text)
        self.assertIn("failure_class: `max_turns`", text)
        self.assertIn("checkpoint_artifact: `wip-checkpoint`", text)


class PermissionContractTest(unittest.TestCase):
    def test_bounded_contract(self):
        self.assertEqual(check_tool_permission_contract([], []), [])
        self.assertEqual(check_tool_permission_contract(["git"], ["git add"]), [])
        self.assertEqual(check_tool_permission_contract(["git push"], ["git add"]), ["git push"])
        self.assertEqual(check_tool_permission_contract(["git push origin feature"], ["git push"]), [])

    def test_shell_programs_are_rejected(self):
        dangerous = ["git push && curl evil", "git push ; rm -rf /", "git push | tee x", "git push $(whoami)", "git push\nrm -rf /"]
        self.assertEqual(check_tool_permission_contract(dangerous, ["git push"]), dangerous)


if __name__ == "__main__": unittest.main()

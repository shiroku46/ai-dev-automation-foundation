"""Direct fixtures for the Queue event admission module."""
from __future__ import annotations

import inspect
import unittest

from scripts.queue_event_guard import resolve_queue_event


BASE = {
    "event_name": "workflow_dispatch",
    "actor": "owner",
    "owner": "owner",
    "ref_name": "main",
    "default_branch": "main",
    "run_attempt": "1",
    "dispatch_issue": "173",
    "dispatch_fingerprint": "",
    "dispatch_attempt": "",
    "comment_issue": "",
    "comment_body": "",
    "comment_is_pr": "false",
}


class QueueEventGuardTest(unittest.TestCase):
    def decision(self, **overrides):
        values = {**BASE, **overrides}
        return resolve_queue_event(**values)

    def test_owner_issue_comment_exact_trigger_is_admitted(self):
        decision = self.decision(
            event_name="issue_comment",
            dispatch_issue="",
            comment_issue="173",
            comment_body=" /claude-run\n",
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.route, "owner-trigger")
        self.assertEqual(decision.issue_number, 173)
        self.assertFalse(decision.automated_retry)

    def test_comment_requires_owner_non_pr_exact_standalone_command(self):
        cases = (
            {"actor": "other"},
            {"comment_is_pr": "true"},
            {"comment_body": "/claude-run extra"},
            {"comment_body": "please /claude-run"},
            {"comment_issue": "0"},
            {"dispatch_issue": "173"},
        )
        for override in cases:
            with self.subTest(override=override):
                decision = self.decision(
                    event_name="issue_comment",
                    dispatch_issue="",
                    comment_issue="173",
                    comment_body="/claude-run",
                    **override,
                )
                self.assertFalse(decision.allowed)

    def test_owner_manual_dispatch_never_needs_event_path(self):
        signature = inspect.signature(resolve_queue_event)
        self.assertNotIn("event_path", signature.parameters)
        decision = self.decision()
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.route, "owner-manual")
        self.assertEqual(decision.issue_number, 173)

    def test_automated_retry_is_exact_and_bounded(self):
        decision = self.decision(
            actor="github-actions[bot]",
            dispatch_fingerprint="a" * 20,
            dispatch_attempt="2",
        )
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.automated_retry)
        self.assertEqual(decision.route, "automated-retry")
        self.assertEqual(decision.fingerprint, "a" * 20)
        self.assertEqual(decision.retry_attempt, 2)

    def test_automated_retry_rejects_partial_or_malformed_identity(self):
        cases = (
            {"dispatch_fingerprint": "", "dispatch_attempt": "1"},
            {"dispatch_fingerprint": "a" * 20, "dispatch_attempt": ""},
            {"dispatch_fingerprint": "A" * 20, "dispatch_attempt": "1"},
            {"dispatch_fingerprint": "a" * 19, "dispatch_attempt": "1"},
            {"dispatch_fingerprint": "a" * 20, "dispatch_attempt": "4"},
            {"run_attempt": "2", "dispatch_fingerprint": "a" * 20, "dispatch_attempt": "1"},
            {"ref_name": "other", "dispatch_fingerprint": "a" * 20, "dispatch_attempt": "1"},
        )
        for override in cases:
            with self.subTest(override=override):
                decision = self.decision(actor="github-actions[bot]", **override)
                self.assertFalse(decision.allowed)

    def test_mixed_comment_and_dispatch_context_fails_closed(self):
        decision = self.decision(comment_issue="173", comment_body="/claude-run")
        self.assertFalse(decision.allowed)
        self.assertIn("mixed", decision.reason)

    def test_unsupported_events_and_malformed_booleans_are_ignored_or_denied(self):
        self.assertFalse(self.decision(event_name="issues").allowed)
        self.assertEqual(self.decision(event_name="issues").route, "ignored")
        decision = self.decision(
            event_name="issue_comment",
            dispatch_issue="",
            comment_issue="173",
            comment_body="/claude-run",
            comment_is_pr="unknown",
        )
        self.assertFalse(decision.allowed)

    def test_scalars_are_bounded_and_non_strings_fail_closed(self):
        cases = (
            {"actor": "x" * 101},
            {"dispatch_issue": 173},
            {"comment_body": "x" * 65, "event_name": "issue_comment", "dispatch_issue": "", "comment_issue": "173"},
            {"event_name": None},
            {"dispatch_issue": "2147483648"},
        )
        for override in cases:
            with self.subTest(override=override):
                self.assertFalse(self.decision(**override).allowed)


if __name__ == "__main__":
    unittest.main()

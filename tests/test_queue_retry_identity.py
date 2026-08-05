"""Direct tests for exact Queue retry record and workflow-run identities."""
from __future__ import annotations

import copy
import unittest

from scripts.queue_retry_identity import (
    exact_retry_runs,
    is_active_ignored_run,
    is_active_queue_run,
    parse_queue_run_title,
    retry_run_matches,
    retry_run_title,
    stranded_retry_attempt,
    validate_retry_records,
)

REPOSITORY = "shiroku46/ai-dev-automation-foundation"
BRANCH = "main"
BASE = "a" * 40
FINGERPRINT = "b" * 20


def retry_record(attempt: int) -> dict:
    return {
        "attempt": attempt,
        "base_sha": BASE,
        "failure_class": "unknown",
        "human_action_required": False,
        "issue_number": 173,
        "next_automatic_action": "dispatch one optional Queue retry",
        "notification": False,
        "request_fingerprint": FINGERPRINT,
        "source_run_id": None,
    }


def retry_run(attempt: int = 1) -> dict:
    return {
        "display_title": retry_run_title(173, attempt, FINGERPRINT),
        "event": "workflow_dispatch",
        "path": ".github/workflows/claude-queue.yml",
        "head_branch": BRANCH,
        "head_sha": BASE,
        "run_attempt": 1,
        "actor": {"login": "github-actions[bot]"},
        "repository": {"full_name": REPOSITORY},
        "status": "completed",
        "conclusion": "failure",
    }


class QueueRunTitleTest(unittest.TestCase):
    def test_retry_title_round_trip(self):
        title = retry_run_title(173, 2, FINGERPRINT)
        self.assertEqual(
            parse_queue_run_title(title),
            {
                "kind": "retry",
                "issue_number": 173,
                "attempt": 2,
                "fingerprint": FINGERPRINT,
            },
        )

    def test_plain_and_ignored_titles(self):
        self.assertEqual(parse_queue_run_title("Optional Claude issue-173 trigger")["kind"], "trigger")
        self.assertEqual(parse_queue_run_title("Optional Claude issue-173 manual")["kind"], "manual")
        self.assertEqual(parse_queue_run_title("Optional Claude ignored issue-173")["kind"], "ignored")
        for value in (
            "Optional Claude issue-173",
            "Optional Claude issue-173 retry-4 request-" + FINGERPRINT,
            "Optional Claude issue-173 retry-1 request-bad",
            "Optional Claude ignored issue-0",
            None,
        ):
            self.assertIsNone(parse_queue_run_title(value))

    def test_title_inputs_are_bounded(self):
        for args in ((0, 1, FINGERPRINT), (173, 4, FINGERPRINT), (173, 1, "bad")):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    retry_run_title(*args)


class ExactRetryRunTest(unittest.TestCase):
    def identity(self) -> dict:
        return {
            "repository": REPOSITORY,
            "default_branch": BRANCH,
            "base_sha": BASE,
            "issue_number": 173,
            "attempt": 1,
            "fingerprint": FINGERPRINT,
        }

    def test_exact_match_and_all_rejections(self):
        source = retry_run()
        self.assertTrue(retry_run_matches(source, **self.identity()))
        mutations = {
            "title": ("display_title", "Optional Claude issue-173 retry-2 request-" + FINGERPRINT),
            "event": ("event", "issue_comment"),
            "path": ("path", ".github/workflows/other.yml"),
            "branch": ("head_branch", "other"),
            "sha": ("head_sha", "c" * 40),
            "run_attempt": ("run_attempt", 2),
        }
        for label, (key, value) in mutations.items():
            changed = copy.deepcopy(source)
            changed[key] = value
            with self.subTest(label=label):
                self.assertFalse(retry_run_matches(changed, **self.identity()))

        changed = copy.deepcopy(source)
        changed["actor"]["login"] = "shiroku46"
        self.assertFalse(retry_run_matches(changed, **self.identity()))
        changed = copy.deepcopy(source)
        changed["repository"]["full_name"] = "other/repo"
        self.assertFalse(retry_run_matches(changed, **self.identity()))

    def test_duplicate_exact_identity_fails_closed(self):
        source = retry_run()
        self.assertEqual(len(exact_retry_runs([source], **self.identity())), 1)
        with self.assertRaises(ValueError):
            exact_retry_runs([source, copy.deepcopy(source)], **self.identity())

    def test_cancelled_still_proves_dispatch(self):
        source = retry_run()
        source["status"] = "completed"
        source["conclusion"] = "cancelled"
        self.assertEqual(len(exact_retry_runs([source], **self.identity())), 1)


class RetryRecordTest(unittest.TestCase):
    def validate(self, records):
        return validate_retry_records(
            records,
            issue_number=173,
            base_sha=BASE,
            fingerprint=FINGERPRINT,
        )

    def test_contiguous_records_and_latest_gap(self):
        records = self.validate([retry_record(1), retry_record(2)])
        self.assertEqual(stranded_retry_attempt(records, [1]), 2)
        self.assertIsNone(stranded_retry_attempt(records, [1, 2]))

    def test_nonlatest_multiple_and_foreign_gaps_fail(self):
        records = self.validate([retry_record(1), retry_record(2), retry_record(3)])
        for dispatched in ([2, 3], [1], [1, 1], [1, 2, 4]):
            with self.subTest(dispatched=dispatched):
                with self.assertRaises(ValueError):
                    stranded_retry_attempt(records, dispatched)

    def test_invalid_record_fields_fail_closed(self):
        base = retry_record(1)
        mutations = {
            "attempt": 2,
            "base_sha": "c" * 40,
            "failure_class": "permission_contract",
            "human_action_required": True,
            "issue_number": 174,
            "next_automatic_action": "other",
            "notification": True,
            "request_fingerprint": "d" * 20,
            "source_run_id": 0,
        }
        for key, value in mutations.items():
            changed = dict(base)
            changed[key] = value
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    self.validate([changed])

    def test_retry_budget_is_three(self):
        self.assertEqual(len(self.validate([retry_record(1), retry_record(2), retry_record(3)])), 3)
        with self.assertRaises(ValueError):
            self.validate([retry_record(1), retry_record(2), retry_record(3), retry_record(4)])


class ActiveRunTest(unittest.TestCase):
    def test_ignored_control_run_is_not_active_implementation(self):
        ignored = {
            "display_title": "Optional Claude ignored issue-173",
            "event": "issue_comment",
            "path": ".github/workflows/claude-queue.yml",
            "status": "in_progress",
        }
        self.assertFalse(is_active_queue_run(ignored))
        self.assertTrue(is_active_ignored_run(ignored, 173))
        self.assertFalse(is_active_ignored_run(ignored, 174))

    def test_retry_run_is_active_only_while_live(self):
        source = retry_run()
        source["status"] = "in_progress"
        self.assertTrue(is_active_queue_run(source))
        source["status"] = "completed"
        self.assertFalse(is_active_queue_run(source))


if __name__ == "__main__":
    unittest.main()

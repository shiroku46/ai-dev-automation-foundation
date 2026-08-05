"""Stable connected Issue hydration regressions."""
from __future__ import annotations

import unittest

from scripts.queue_issue_hydration import resolve_stable_issue


def issue(*, number=173, state="open", author="shiroku46", pull_request=None, updated="2026-08-05T05:00:00Z"):
    return {
        "node_id": f"issue-{number}",
        "number": number,
        "state": state,
        "user": {"login": author},
        "pull_request": pull_request,
        "updated_at": updated,
    }


class QueueIssueHydrationTest(unittest.TestCase):
    def resolve(self, samples):
        return resolve_stable_issue(samples, number=173, allowed_owners={"shiroku46"})

    def test_two_matching_open_issue_reads_are_trusted(self):
        status, value, predicates = self.resolve([issue(), issue()])
        self.assertEqual(status, "trusted")
        self.assertEqual(value["number"], 173)
        self.assertTrue(all(predicates.values()))

    def test_closed_issue_is_stably_rejected(self):
        status, value, predicates = self.resolve([issue(state="closed"), issue(state="closed")])
        self.assertEqual(status, "rejected")
        self.assertIsNone(value)
        self.assertFalse(predicates["open_state"])

    def test_pr_shaped_response_is_stably_rejected(self):
        shaped = issue(pull_request={"url": "https://api.github.test/pulls/173"})
        status, value, predicates = self.resolve([shaped, shaped])
        self.assertEqual(status, "rejected")
        self.assertIsNone(value)
        self.assertFalse(predicates["issue_not_pr"])

    def test_repeated_transient_rejection_yields_to_later_trusted_pair(self):
        bad = issue(state="closed", pull_request={"url": "x"}, updated="2026-08-05T04:59:00Z")
        good = issue()
        status, value, predicates = self.resolve([bad, bad, good, good])
        self.assertEqual(status, "trusted")
        self.assertEqual(value, good)
        self.assertTrue(all(predicates.values()))

    def test_persistent_identity_disagreement_is_unstable(self):
        first = issue(updated="2026-08-05T05:00:00Z")
        second = issue(updated="2026-08-05T05:00:01Z")
        third = issue(updated="2026-08-05T05:00:02Z")
        status, value, _ = self.resolve([first, second, third])
        self.assertEqual(status, "unstable")
        self.assertIsNone(value)

    def test_wrong_number_and_wrong_author_are_rejected(self):
        for bad in (issue(number=174), issue(author="other")):
            with self.subTest(bad=bad):
                status, value, predicates = self.resolve([bad, bad])
                self.assertEqual(status, "rejected")
                self.assertIsNone(value)
                self.assertFalse(all(predicates.values()))

    def test_incomplete_or_non_object_samples_do_not_stabilize(self):
        status, value, _ = self.resolve([None, {}, {"number": 173}, "bad"])
        self.assertEqual(status, "unstable")
        self.assertIsNone(value)

    def test_match_bound_is_fixed(self):
        with self.assertRaises(ValueError):
            resolve_stable_issue([], number=173, allowed_owners={"shiroku46"}, required_matches=1)
        with self.assertRaises(ValueError):
            resolve_stable_issue([], number=173, allowed_owners={"shiroku46"}, required_matches=5)


if __name__ == "__main__":
    unittest.main()

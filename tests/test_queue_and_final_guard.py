import importlib
import os
import sys
import unittest
from unittest.mock import Mock, patch
from pathlib import Path

from scripts.supervisor_policy import is_protected

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHA = "d" * 40
CANDIDATE_SHA = "c" * 40
ISSUE_NUMBER = 129


class QueueAndFinalGuardTest(unittest.TestCase):
    def test_queue_failure_is_non_notifying_and_recovery_is_separated(self):
        queue = (ROOT / ".github/workflows/claude-queue.yml").read_text(encoding="utf-8")
        finalize = queue.split("\n  finalize:\n", 1)[1]
        self.assertNotIn("QUEUE_PIPELINE_FAILED", finalize)
        self.assertNotIn("gh issue comment", finalize)
        self.assertIn("notification: false", finalize)

        reconcile = (ROOT / ".github/workflows/ci-reconcile.yml").read_text(encoding="utf-8")
        self.assertIn('workflows: ["CI", "Unit Tests", "Claude Issue Queue"]', reconcile)
        self.assertIn("python -m scripts.supervisor_queue_recovery_v3", reconcile)

        supervisor = (ROOT / ".github/workflows/supervisor.yml").read_text(encoding="utf-8")
        self.assertIn("python -m scripts.supervisor_final_guard", supervisor)
        self.assertIn("actions: read", supervisor)
        self.assertNotIn("actions: write", supervisor)

    def test_merge_capable_guard_is_protected(self):
        self.assertTrue(is_protected("scripts/supervisor_final_guard.py"))

    def _load_guard(self):
        environment = {
            "REPOSITORY": "example/foundation",
            "DEFAULT_BRANCH": "main",
            "AUTOMATION_OWNER": "owner",
        }
        with patch.dict(os.environ, environment, clear=False):
            sys.modules.pop("scripts.supervisor_final_guard", None)
            sys.modules.pop("scripts.supervisor_runtime", None)
            return importlib.import_module("scripts.supervisor_final_guard")

    @staticmethod
    def _live_pr(number=10, head_sha=CANDIDATE_SHA, draft=False):
        return {
            "number": number,
            "state": "open",
            "draft": draft,
            "mergeable": True,
            "head": {
                "sha": head_sha,
                "ref": "fix/issue-129",
                "repo": {"full_name": "example/foundation"},
            },
            "base": {"ref": "main", "repo": {"full_name": "example/foundation"}},
            "user": {"login": "owner"},
            "labels": [],
            "body": "Closes #129",
        }

    def test_low_risk_is_clean_without_provider_review(self):
        guard = self._load_guard()
        with patch.object(guard, "_risk_for_pr", return_value=("low", ISSUE_NUMBER)), patch.object(
            guard, "_original_exact_codex_evidence", return_value={"state": "pending", "timestamp": None, "request_timestamp": None}
        ):
            evidence = guard.review_evidence(10, CANDIDATE_SHA)
        self.assertEqual(evidence["state"], "clean")
        self.assertEqual(evidence["review_source"], "low-risk-checks")

    def test_standard_risk_accepts_trusted_nonempty_coordinator_marker(self):
        guard = self._load_guard()
        comment = {
            "user": {"login": "owner"},
            "created_at": "2026-08-01T00:00:00Z",
            "updated_at": "2026-08-01T00:00:00Z",
            "body": (
                f"<!-- foundation-coordinator-review:{CANDIDATE_SHA}:clean -->\n"
                "Reviewed exact diff and green required checks; no blocking issue."
            ),
        }
        with patch.object(guard, "_risk_for_pr", return_value=("standard", ISSUE_NUMBER)), patch.object(
            guard, "_original_exact_codex_evidence", return_value={"state": "pending", "timestamp": None, "request_timestamp": None}
        ), patch.object(guard.runtime, "api_list", return_value=[comment]):
            evidence = guard.review_evidence(10, CANDIDATE_SHA)
        self.assertEqual(evidence["state"], "clean")
        self.assertEqual(evidence["review_source"], "coordinator")

    def test_coordinator_marker_rejects_untrusted_edited_empty_or_stale(self):
        guard = self._load_guard()
        marker = f"<!-- foundation-coordinator-review:{CANDIDATE_SHA}:clean -->"
        invalid = [
            {"user": {"login": "outsider"}, "created_at": "a", "updated_at": "a", "body": marker + "\nsummary"},
            {"user": {"login": "owner"}, "created_at": "a", "updated_at": "b", "body": marker + "\nsummary"},
            {"user": {"login": "owner"}, "created_at": "a", "updated_at": "a", "body": marker},
            {"user": {"login": "owner"}, "created_at": "a", "updated_at": "a", "body": f"<!-- foundation-coordinator-review:{'e' * 40}:clean -->\nsummary"},
        ]
        for item in invalid:
            with self.subTest(item=item):
                with patch.object(guard.runtime, "api_list", return_value=[item]):
                    self.assertIsNone(guard._coordinator_review(10, CANDIDATE_SHA))

    def test_protected_risk_requires_codex(self):
        guard = self._load_guard()
        with patch.object(guard, "_risk_for_pr", return_value=("protected", ISSUE_NUMBER)), patch.object(
            guard, "_original_exact_codex_evidence", return_value={"state": "pending", "timestamp": None, "request_timestamp": None}
        ), patch.object(guard, "_provider_route_unavailable", return_value=False):
            evidence = guard.review_evidence(10, CANDIDATE_SHA)
        self.assertEqual(evidence["state"], "pending")
        self.assertEqual(evidence["risk"], "protected")

    def test_provider_setup_response_is_unavailable_not_review(self):
        guard = self._load_guard()
        with patch.object(guard, "_risk_for_pr", return_value=("standard", ISSUE_NUMBER)), patch.object(
            guard, "_original_exact_codex_evidence", return_value={"state": "pending", "timestamp": None, "request_timestamp": None}
        ), patch.object(guard, "_coordinator_review", return_value=None), patch.object(
            guard, "_provider_route_unavailable", return_value=True
        ):
            evidence = guard.review_evidence(10, CANDIDATE_SHA)
        self.assertEqual(evidence["state"], "pending")
        self.assertEqual(evidence["review_route"], "unavailable")

    def test_actions_records_neutral_review_required_marker(self):
        guard = self._load_guard()
        posted = Mock()
        with patch.object(guard, "_risk_for_pr", return_value=("protected", ISSUE_NUMBER)), patch.object(
            guard.runtime, "api_list", return_value=[]
        ), patch.object(guard.runtime, "comment", posted):
            guard.record_review_required(10, CANDIDATE_SHA)
        body = posted.call_args.args[1]
        self.assertIn(f"foundation-review-required:{CANDIDATE_SHA}:protected", body)
        self.assertNotIn("@codex", body)

    def test_guard_stores_stable_gate_after_checks_and_scope(self):
        guard = self._load_guard()
        live = self._live_pr(12)
        with patch.object(guard, "_original_current_default_sha", return_value=DEFAULT_SHA), patch.object(
            guard.runtime, "attestation_attempts", return_value=[{"success": True}]
        ), patch.object(guard, "_native_workflow_evidence", return_value=(True, ["native"])), patch.object(
            guard.runtime, "api", return_value=live
        ), patch.object(guard, "source_and_scope_minimum", return_value=(ISSUE_NUMBER, {}, ["docs/a.md"], None)):
            self.assertEqual(
                guard.guarded_native_workflow_evidence(CANDIDATE_SHA, 12),
                (True, ["native"]),
            )
        self.assertEqual(guard._verified_gate, (CANDIDATE_SHA, 12, DEFAULT_SHA, ISSUE_NUMBER))

    def test_rejected_pre_mutation_merge_does_not_consume_gate(self):
        guard = self._load_guard()
        guard._verified_gate = (CANDIDATE_SHA, 20, DEFAULT_SHA, ISSUE_NUMBER)
        wrong = (
            "api", "--method", "PUT",
            "repos/example/foundation/pulls/21/merge",
            "-f", "merge_method=squash",
            "-f", f"sha={CANDIDATE_SHA}",
        )
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            guard.guarded_gh(*wrong)
        self.assertEqual(guard._verified_gate, (CANDIDATE_SHA, 20, DEFAULT_SHA, ISSUE_NUMBER))

    def test_successful_expected_head_merge_consumes_gate(self):
        guard = self._load_guard()
        guard._verified_gate = (CANDIDATE_SHA, 20, DEFAULT_SHA, ISSUE_NUMBER)
        args = (
            "api", "--method", "PUT",
            "repos/example/foundation/pulls/20/merge",
            "-f", "merge_method=squash",
            "-f", f"sha={CANDIDATE_SHA}",
        )
        live = self._live_pr(20)
        delegated = Mock(return_value="merged")
        with patch.object(guard.runtime, "api", return_value=live), patch.object(
            guard, "source_and_scope_minimum", return_value=(ISSUE_NUMBER, {}, ["docs/a.md"], None)
        ), patch.object(guard, "_original_current_default_sha", return_value=DEFAULT_SHA), patch.object(
            guard, "_original_gh", delegated
        ):
            self.assertEqual(guard.guarded_gh(*args), "merged")
        self.assertIsNone(guard._verified_gate)
        delegated.assert_called_once_with(*args)

    def test_main_installs_minimum_safety_overrides(self):
        guard = self._load_guard()
        delegated = Mock(return_value=0)
        with patch.object(guard.runtime, "main", delegated):
            self.assertEqual(guard.main(), 0)
        self.assertIs(guard.runtime.source_and_scope, guard.source_and_scope_minimum)
        self.assertIs(guard.runtime.exact_codex_evidence, guard.review_evidence)
        self.assertIs(guard.runtime.request_codex, guard.record_review_required)
        self.assertIs(guard.runtime.native_workflow_evidence, guard.guarded_native_workflow_evidence)
        self.assertIs(guard.runtime.gh, guard.guarded_gh)


if __name__ == "__main__":
    unittest.main()

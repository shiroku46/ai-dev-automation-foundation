import importlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.supervisor_policy import is_protected

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHA = "d" * 40
CANDIDATE_SHA = "c" * 40
ISSUE_NUMBER = 85


class QueueAndFinalGuardTest(unittest.TestCase):
    def test_queue_failure_is_non_notifying_and_supervisor_reconciles(self):
        queue = (ROOT / ".github/workflows/claude-queue.yml").read_text(encoding="utf-8")
        finalize = queue.split("\n  finalize:\n", 1)[1]
        self.assertNotIn("QUEUE_PIPELINE_FAILED", finalize)
        self.assertNotIn("gh issue comment", finalize)
        self.assertNotIn("--add-label ai-blocked", finalize)
        self.assertIn("notification: false", finalize)
        self.assertIn("GITHUB_STEP_SUMMARY", finalize)

        reconcile = (ROOT / ".github/workflows/ci-reconcile.yml").read_text(encoding="utf-8")
        self.assertIn('workflows: ["CI", "Unit Tests", "Claude Issue Queue"]', reconcile)
        self.assertIn("\n  queue_recovery:\n", reconcile)
        self.assertIn("python -m scripts.supervisor_queue_recovery_v3", reconcile)
        recovery = reconcile.split("\n  queue_recovery:\n", 1)[1]
        self.assertIn("actions: write", recovery)
        self.assertIn("contents: write", recovery)
        self.assertIn("issues: read", recovery)
        self.assertIn("pull-requests: read", recovery)

        supervisor = (ROOT / ".github/workflows/supervisor.yml").read_text(encoding="utf-8")
        self.assertIn('"Claude Issue Queue"', supervisor)
        self.assertIn("actions: read", supervisor)
        self.assertIn("python -m scripts.supervisor_final_guard", supervisor)
        self.assertNotIn("actions: write", supervisor)
        self.assertNotIn("python -m scripts.supervisor_queue_recovery_v3", supervisor)

    def test_merge_capable_guard_is_a_protected_path(self):
        self.assertTrue(is_protected("scripts/supervisor_final_guard.py"))

    def _load_guard(self):
        environment = {
            "REPOSITORY": "example/foundation-e2e",
            "DEFAULT_BRANCH": "main",
            "AUTOMATION_OWNER": "owner",
        }
        with patch.dict(os.environ, environment, clear=False):
            sys.modules.pop("scripts.supervisor_final_guard", None)
            sys.modules.pop("scripts.supervisor_runtime", None)
            return importlib.import_module("scripts.supervisor_final_guard")

    def _trusted_live_pr(self, number: int, *, labels=None, head_sha=CANDIDATE_SHA, state="open", draft=False):
        return {
            "number": number,
            "state": state,
            "draft": draft,
            "mergeable": True,
            "head": {"sha": head_sha, "ref": "fix/candidate", "repo": {"full_name": "example/foundation-e2e"}},
            "base": {"ref": "main", "repo": {"full_name": "example/foundation-e2e"}},
            "user": {"login": "owner"},
            "labels": list(labels or []),
        }

    @staticmethod
    def _scope_result(issue_number=ISSUE_NUMBER, error=None):
        issue = {"number": issue_number, "user": {"login": "owner"}}
        return issue_number, issue, ["scripts/supervisor_final_guard.py"], error

    def test_guard_fails_closed_without_current_successful_attestation(self):
        guard = self._load_guard()
        native = Mock(return_value=(True, ["native-run"]))
        with patch.object(guard, "_original_current_default_sha", return_value=DEFAULT_SHA), patch.object(
            guard.runtime, "attestation_attempts", return_value=[{"success": False}]
        ), patch.object(guard, "_native_workflow_evidence", native):
            self.assertEqual(guard.guarded_native_workflow_evidence(CANDIDATE_SHA, 12), (False, []))
        native.assert_not_called()
        self.assertIsNone(guard._verified_gate)

    def test_guard_binds_attestation_native_and_source_to_same_default_sha(self):
        guard = self._load_guard()
        observed = []

        def attempts(_sha):
            observed.append(("attestation", guard.runtime.current_default_sha()))
            return [{"success": True}]

        def native(_sha, _pr_number):
            observed.append(("native", guard.runtime.current_default_sha()))
            return True, ["native-run"]

        live_pr = self._trusted_live_pr(13)
        with patch.object(guard, "_original_current_default_sha", return_value=DEFAULT_SHA), patch.object(
            guard.runtime, "attestation_attempts", side_effect=attempts
        ), patch.object(guard, "_native_workflow_evidence", side_effect=native), patch.object(
            guard.runtime, "api", return_value=live_pr
        ), patch.object(guard.runtime, "source_and_scope", return_value=self._scope_result()) as scope:
            self.assertEqual(guard.guarded_native_workflow_evidence(CANDIDATE_SHA, 13), (True, ["native-run"]))
        self.assertEqual(observed, [("attestation", DEFAULT_SHA), ("native", DEFAULT_SHA)])
        scope.assert_called_once_with(live_pr)
        self.assertEqual(guard._verified_gate, (CANDIDATE_SHA, 13, DEFAULT_SHA, ISSUE_NUMBER))

    def test_guard_rejects_default_movement_during_native_gate(self):
        guard = self._load_guard()

        def attempts(_sha):
            guard.runtime.current_default_sha()
            return [{"success": True}]

        def native(_sha, _pr_number):
            guard.runtime.current_default_sha()
            return True, ["native-run"]

        with patch.object(guard, "_original_current_default_sha", side_effect=[DEFAULT_SHA, DEFAULT_SHA, "e" * 40]), patch.object(
            guard.runtime, "attestation_attempts", side_effect=attempts
        ), patch.object(guard, "_native_workflow_evidence", side_effect=native):
            self.assertEqual(guard.guarded_native_workflow_evidence(CANDIDATE_SHA, 14), (False, []))
        self.assertIsNone(guard._verified_gate)

    def test_guard_rejects_source_authorization_before_storing_gate(self):
        guard = self._load_guard()
        live_pr = self._trusted_live_pr(14)
        with patch.object(guard, "_original_current_default_sha", return_value=DEFAULT_SHA), patch.object(
            guard.runtime, "attestation_attempts", return_value=[{"success": True}]
        ), patch.object(guard, "_native_workflow_evidence", return_value=(True, ["native-run"])), patch.object(
            guard.runtime, "api", return_value=live_pr
        ), patch.object(
            guard.runtime, "source_and_scope", return_value=self._scope_result(error="UNAUTHORIZED_PROTECTED_PATH")
        ):
            self.assertEqual(guard.guarded_native_workflow_evidence(CANDIDATE_SHA, 14), (False, []))
        self.assertIsNone(guard._verified_gate)

    def test_guard_rejects_missing_or_null_label_evidence_before_storing_gate(self):
        for labels_marker in ("missing", None):
            with self.subTest(labels=labels_marker):
                guard = self._load_guard()
                live_pr = self._trusted_live_pr(14)
                if labels_marker == "missing":
                    live_pr.pop("labels")
                else:
                    live_pr["labels"] = None
                with patch.object(guard, "_original_current_default_sha", return_value=DEFAULT_SHA), patch.object(
                    guard.runtime, "attestation_attempts", return_value=[{"success": True}]
                ), patch.object(guard, "_native_workflow_evidence", return_value=(True, ["native-run"])), patch.object(
                    guard.runtime, "api", return_value=live_pr
                ):
                    self.assertEqual(guard.guarded_native_workflow_evidence(CANDIDATE_SHA, 14), (False, []))
                self.assertIsNone(guard._verified_gate)

    def test_merge_guard_requires_matching_candidate_pr_default_and_source(self):
        guard = self._load_guard()
        delegated = Mock(return_value="merged")
        guard._verified_gate = (CANDIDATE_SHA, 15, DEFAULT_SHA, ISSUE_NUMBER)
        args = ("api", "--method", "PUT", "repos/example/foundation-e2e/pulls/15/merge", "-f", "merge_method=squash", "-f", f"sha={CANDIDATE_SHA}")
        live_pr = self._trusted_live_pr(15)
        with patch.object(guard, "_original_current_default_sha", return_value=DEFAULT_SHA), patch.object(
            guard.runtime, "api", return_value=live_pr
        ) as live, patch.object(guard.runtime, "source_and_scope", return_value=self._scope_result()) as scope, patch.object(
            guard, "_original_gh", delegated
        ):
            self.assertEqual(guard.guarded_gh(*args), "merged")
        live.assert_called_once_with("repos/example/foundation-e2e/pulls/15")
        scope.assert_called_once_with(live_pr)
        delegated.assert_called_once_with(*args)
        self.assertIsNone(guard._verified_gate)

    def test_failed_merge_attempt_consumes_gate(self):
        guard = self._load_guard()
        guard._verified_gate = (CANDIDATE_SHA, 20, DEFAULT_SHA, ISSUE_NUMBER)
        args = ("api", "--method", "PUT", "repos/example/foundation-e2e/pulls/21/merge", "-f", f"sha={CANDIDATE_SHA}")
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            guard.guarded_gh(*args)
        self.assertIsNone(guard._verified_gate)
        with self.assertRaisesRegex(RuntimeError, "no verified"):
            guard.guarded_gh(*args)

    def test_merge_guard_rejects_mismatch_and_final_default_movement(self):
        guard = self._load_guard()
        delegated = Mock(return_value="merged")
        args = ("api", "--method", "PUT", "repos/example/foundation-e2e/pulls/16/merge", "-f", f"sha={CANDIDATE_SHA}")
        guard._verified_gate = (CANDIDATE_SHA, 15, DEFAULT_SHA, ISSUE_NUMBER)
        with patch.object(guard, "_original_gh", delegated):
            with self.assertRaisesRegex(RuntimeError, "does not match"):
                guard.guarded_gh(*args)
        self.assertIsNone(guard._verified_gate)
        delegated.assert_not_called()
        guard._verified_gate = (CANDIDATE_SHA, 16, DEFAULT_SHA, ISSUE_NUMBER)
        with patch.object(guard, "_original_current_default_sha", return_value="e" * 40), patch.object(
            guard.runtime, "api", return_value=self._trusted_live_pr(16)
        ), patch.object(guard.runtime, "source_and_scope", return_value=self._scope_result()), patch.object(
            guard, "_original_gh", delegated
        ):
            with self.assertRaisesRegex(RuntimeError, "moved"):
                guard.guarded_gh(*args)
        self.assertIsNone(guard._verified_gate)
        delegated.assert_not_called()

    def test_merge_guard_rejects_live_ai_no_merge_or_head_movement(self):
        guard = self._load_guard()
        delegated = Mock(return_value="merged")
        args = ("api", "--method", "PUT", "repos/example/foundation-e2e/pulls/17/merge", "-f", f"sha={CANDIDATE_SHA}")
        guard._verified_gate = (CANDIDATE_SHA, 17, DEFAULT_SHA, ISSUE_NUMBER)
        blocked = self._trusted_live_pr(17, labels=[{"name": "ai-no-merge"}])
        with patch.object(guard.runtime, "api", return_value=blocked), patch.object(guard, "_original_gh", delegated):
            with self.assertRaisesRegex(RuntimeError, "trusted candidate"):
                guard.guarded_gh(*args)
        self.assertIsNone(guard._verified_gate)
        delegated.assert_not_called()
        guard._verified_gate = (CANDIDATE_SHA, 17, DEFAULT_SHA, ISSUE_NUMBER)
        moved = self._trusted_live_pr(17, head_sha="e" * 40)
        with patch.object(guard.runtime, "api", return_value=moved), patch.object(guard, "_original_gh", delegated):
            with self.assertRaisesRegex(RuntimeError, "trusted candidate"):
                guard.guarded_gh(*args)
        self.assertIsNone(guard._verified_gate)
        delegated.assert_not_called()

    def test_merge_guard_rejects_incomplete_closed_draft_or_label_evidence(self):
        guard = self._load_guard()
        delegated = Mock(return_value="merged")
        args = ("api", "--method", "PUT", "repos/example/foundation-e2e/pulls/18/merge", "-f", f"sha={CANDIDATE_SHA}")
        missing_labels = self._trusted_live_pr(18)
        missing_labels.pop("labels")
        null_labels = self._trusted_live_pr(18)
        null_labels["labels"] = None
        for live in (
            self._trusted_live_pr(18, state="closed"),
            self._trusted_live_pr(18, draft=True),
            self._trusted_live_pr(18, draft=None),
            missing_labels,
            null_labels,
        ):
            with self.subTest(state=live["state"], draft=live.get("draft"), labels=live.get("labels", "missing")):
                guard._verified_gate = (CANDIDATE_SHA, 18, DEFAULT_SHA, ISSUE_NUMBER)
                with patch.object(guard.runtime, "api", return_value=live), patch.object(guard, "_original_gh", delegated):
                    with self.assertRaises(RuntimeError):
                        guard.guarded_gh(*args)
                self.assertIsNone(guard._verified_gate)
        delegated.assert_not_called()

    def test_merge_guard_rejects_source_issue_or_authorization_movement(self):
        guard = self._load_guard()
        delegated = Mock(return_value="merged")
        args = ("api", "--method", "PUT", "repos/example/foundation-e2e/pulls/19/merge", "-f", f"sha={CANDIDATE_SHA}")
        live_pr = self._trusted_live_pr(19)
        guard._verified_gate = (CANDIDATE_SHA, 19, DEFAULT_SHA, ISSUE_NUMBER)
        with patch.object(guard.runtime, "api", return_value=live_pr), patch.object(
            guard.runtime, "source_and_scope", return_value=self._scope_result(error="UNAUTHORIZED_CHANGED_PATH")
        ), patch.object(guard, "_original_gh", delegated):
            with self.assertRaisesRegex(RuntimeError, "authorization"):
                guard.guarded_gh(*args)
        self.assertIsNone(guard._verified_gate)
        delegated.assert_not_called()
        guard._verified_gate = (CANDIDATE_SHA, 19, DEFAULT_SHA, ISSUE_NUMBER)
        with patch.object(guard.runtime, "api", return_value=live_pr), patch.object(
            guard.runtime, "source_and_scope", return_value=self._scope_result(86)
        ), patch.object(guard, "_original_gh", delegated):
            with self.assertRaisesRegex(RuntimeError, "source Issue"):
                guard.guarded_gh(*args)
        self.assertIsNone(guard._verified_gate)
        delegated.assert_not_called()

    def test_unrelated_gh_calls_pass_through(self):
        guard = self._load_guard()
        delegated = Mock(return_value="ok")
        with patch.object(guard, "_original_gh", delegated):
            self.assertEqual(guard.guarded_gh("api", "repos/example/foundation-e2e"), "ok")
        delegated.assert_called_once_with("api", "repos/example/foundation-e2e")

    def test_machine_codex_marker_is_non_notifying(self):
        guard = self._load_guard()
        with patch.object(guard.runtime, "api_list", return_value=[]), patch.object(
            guard.runtime, "comment"
        ) as comment:
            guard.request_codex_without_provider_mention(22, CANDIDATE_SHA)
        body = comment.call_args.args[1]
        self.assertNotIn("@codex", body)
        self.assertIn("notification: `false`", body)
        self.assertIn("required_human_action: `null`", body)

    def test_main_installs_all_final_guards_before_runtime(self):
        guard = self._load_guard()
        delegated = Mock(return_value=0)
        with patch.object(guard.runtime, "main", delegated):
            self.assertEqual(guard.main(), 0)
        self.assertIs(guard.runtime.native_workflow_evidence, guard.guarded_native_workflow_evidence)
        self.assertIs(guard.runtime.gh, guard.guarded_gh)
        self.assertIs(guard.runtime.request_codex, guard.request_codex_without_provider_mention)
        delegated.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

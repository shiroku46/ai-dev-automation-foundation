import importlib
import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

ENVIRONMENT = {
    "REPOSITORY": "example/foundation",
    "DEFAULT_BRANCH": "main",
    "AUTOMATION_OWNER": "owner",
}


def load_runtime():
    with patch.dict(os.environ, ENVIRONMENT, clear=False):
        sys.modules.pop("scripts.supervisor_runtime", None)
        return importlib.import_module("scripts.supervisor_runtime")


class RuntimeHumanNoticeTest(unittest.TestCase):
    def setUp(self):
        self.runtime = load_runtime()
        self.sha = "a" * 40
        self.pr = {
            "number": 7,
            "state": "open",
            "head": {"sha": self.sha},
            "changed_files": 1,
            "mergeable": True,
            "mergeable_state": "clean",
        }

    def valid_fields(self, **overrides):
        values = {
            "reason": "HUMAN_ONLY_ACCOUNT_LEVEL_REPOSITORY_CREATION_UI_UNAVAILABLE",
            "issue_number": 58,
            "pr_number": 62,
            "exact_head_sha": self.sha,
            "attempted_connected_paths": [
                "GitHub App get_repo for both exact targets",
                "GitHub App installation repository visibility listing",
            ],
            "impossibility_evidence": [
                "Both exact repositories are absent from the connected installation.",
                "Repository creation is unavailable through the connected GitHub actions.",
            ],
            "provider_ui_action": self.runtime.HUMAN_ONLY_ACTIONS[
                "HUMAN_ONLY_ACCOUNT_LEVEL_REPOSITORY_CREATION_UI_UNAVAILABLE"
            ],
            "automatic_resume_condition": (
                "Both exact repositories exist and are visible to the connected GitHub App."
            ),
            "targets": [
                "shiroku46/ai-dev-automation-foundation",
                "shiroku46/ai-dev-automation-foundation-e2e",
            ],
        }
        values.update(overrides)
        return values

    def valid_notice(self, **overrides):
        return self.runtime.format_human_only_notice(**self.valid_fields(**overrides))

    def notice_pr(self, *, sha=None, issue_number=58, state="open"):
        return {
            "number": 62,
            "state": state,
            "head": {
                "sha": sha or self.sha,
                "ref": "automation/notice",
                "repo": {"full_name": "example/foundation"},
            },
            "base": {
                "ref": "main",
                "repo": {"full_name": "example/foundation"},
            },
            "user": {"login": "owner"},
            "labels": [],
            "body": f"Closes #{issue_number}.",
        }

    def test_repository_creation_notice_contains_exact_audited_contract(self):
        body = self.valid_notice()
        self.assertIn("notification: `true`", body)
        self.assertIn("reason_code: `HUMAN_ONLY_ACCOUNT_LEVEL_REPOSITORY_CREATION_UI_UNAVAILABLE`", body)
        self.assertIn("issue: `#58`", body)
        self.assertIn("pull_request: `#62`", body)
        self.assertIn(self.sha, body)
        self.assertIn("shiroku46/ai-dev-automation-foundation", body)
        self.assertIn("shiroku46/ai-dev-automation-foundation-e2e", body)
        self.assertIn("attempted_connected_paths", body)
        self.assertIn("impossibility_evidence", body)
        self.assertIn("required_provider_ui_action", body)
        self.assertIn("automatic_resume_condition", body)

    def test_only_three_human_only_reason_families_are_accepted(self):
        self.assertEqual(len(self.runtime.HUMAN_ONLY_REASONS), 3)
        for rejected in (
            "TRUSTED_ATTESTATION_RETRY_EXHAUSTED",
            "NO_MEANINGFUL_PROGRESS",
            "MERGE_NOT_READY",
            "UNAUTHORIZED_PROTECTED_PATH",
            "UNTRUSTED_EVIDENCE",
            "AMBIGUOUS_TECHNICAL_STATE",
            "PERMISSION_DECLARATION_ONLY",
        ):
            with self.subTest(rejected=rejected):
                with self.assertRaises(ValueError):
                    self.valid_notice(reason=rejected)

    def test_notice_fails_closed_on_missing_or_mismatched_evidence(self):
        invalid = (
            {"issue_number": 0},
            {"pr_number": -1},
            {"exact_head_sha": "abc"},
            {"attempted_connected_paths": []},
            {"impossibility_evidence": []},
            {"provider_ui_action": "Press Merge"},
            {"automatic_resume_condition": ""},
            {"targets": ["only/one"]},
        )
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValueError):
                    self.valid_notice(**overrides)

    def test_other_human_only_families_require_their_canonical_action(self):
        cases = (
            ("HUMAN_ONLY_CREDENTIAL_PROVIDER_UI_REQUIRED", ["provider/account"]),
            ("HUMAN_ONLY_DISCONNECTED_INTEGRATION_RECONNECTION_UI_REQUIRED", ["provider/integration"]),
        )
        for reason, targets in cases:
            with self.subTest(reason=reason):
                body = self.valid_notice(
                    reason=reason,
                    provider_ui_action=self.runtime.HUMAN_ONLY_ACTIONS[reason],
                    targets=targets,
                )
                self.assertIn(f"reason_code: `{reason}`", body)

    def test_self_resolution_audit_performs_connected_queries_and_final_head_check(self):
        observed_paths = []

        def fake_api(path):
            observed_paths.append(path)
            if path == "repos/example/foundation":
                return {"visibility": "public", "default_branch": "main"}
            if path == "repos/example/foundation/pulls/7":
                return self.pr
            if path == "repos/example/foundation/collaborators/owner/permission":
                return {"permission": "admin"}
            if path == "repos/example/foundation/issues/5":
                return {"state": "open", "user": {"login": "owner"}, "body": ""}
            if "/actions/workflows/" in path:
                return {"id": len(observed_paths), "state": "active"}
            raise AssertionError(path)

        with (
            patch.object(self.runtime, "api", side_effect=fake_api),
            patch.object(self.runtime, "changed_paths", return_value=["probe.py"]),
            patch.object(self.runtime, "attestation_attempts", return_value=[{"run_id": 9, "active": False, "success": False, "complete": True}]),
            patch.object(self.runtime, "exact_codex_state", return_value="blocking"),
            patch.object(self.runtime, "unresolved_review_threads", return_value=True),
        ):
            audit = self.runtime.self_resolution_audit(self.pr, 5, "BLOCKING_CODEX_REVIEW")

        self.assertIn("repos/example/foundation", observed_paths)
        self.assertGreaterEqual(observed_paths.count("repos/example/foundation/pulls/7"), 2)
        self.assertIn("repos/example/foundation/issues/5", observed_paths)
        self.assertIn("repos/example/foundation/collaborators/owner/permission", observed_paths)
        for workflow in self.runtime.AUDIT_WORKFLOWS:
            self.assertIn(f"repos/example/foundation/actions/workflows/{workflow}", observed_paths)
        self.assertIn("run_id", audit["workflow_run_and_job_evidence"])
        self.assertIn("codex=blocking", audit["review_and_provenance"])
        self.assertIn("permission=admin", audit["permissions_and_credentials"])
        self.assertIn("trusted-checks.yml:active", audit["alternative_connected_paths"])
        self.assertIn("mergeable=True", audit["mergeability"])
        self.assertIn("initial_and_final_head_confirmed=true", audit["repository_metadata"])

    def test_merge_not_ready_audit_requires_live_terminal_mergeability(self):
        terminal = {**self.pr, "mergeable": False, "mergeable_state": "dirty"}
        with (
            patch.object(self.runtime, "api", side_effect=lambda path: (
                {"visibility": "public", "default_branch": "main"}
                if path == "repos/example/foundation"
                else terminal
                if path == "repos/example/foundation/pulls/7"
                else {"permission": "admin"}
                if path == "repos/example/foundation/collaborators/owner/permission"
                else {"state": "open", "user": {"login": "owner"}, "body": ""}
                if path == "repos/example/foundation/issues/5"
                else {"id": 1, "state": "active"}
            )),
            patch.object(self.runtime, "changed_paths", return_value=["probe.py"]),
            patch.object(self.runtime, "attestation_attempts", return_value=[]),
            patch.object(self.runtime, "exact_codex_state", return_value="clean"),
            patch.object(self.runtime, "unresolved_review_threads", return_value=False),
        ):
            audit = self.runtime.self_resolution_audit(terminal, 5, "MERGE_NOT_READY")
        self.assertIn("mergeable=False", audit["mergeability"])
        self.assertIn("mergeable_state=dirty", audit["mergeability"])

    def test_internal_stop_is_non_notifying_audited_and_deduplicated(self):
        posted = []
        labels = []
        audit = {
            "workflow_run_and_job_evidence": "queried",
            "alternative_connected_paths": "queried",
            "exact_head_sha": self.sha,
        }
        with (
            patch.object(self.runtime, "self_resolution_audit", return_value=audit),
            patch.object(self.runtime, "_live_pr", return_value=self.pr),
            patch.object(self.runtime, "api_list", side_effect=[[], [{"body": f"{self.runtime.STOP_PREFIX}NO_MEANINGFUL_PROGRESS:{self.sha} -->"}]]),
            patch.object(self.runtime, "comment", side_effect=lambda number, body: posted.append((number, body))),
            patch.object(self.runtime, "ensure_label", side_effect=lambda *args: labels.append(args)),
        ):
            self.runtime.stop_report(
                self.pr,
                5,
                "NO_MEANINGFUL_PROGRESS",
                "No trusted evidence changed during the bounded interval.",
            )
            self.runtime.stop_report(
                self.pr,
                5,
                "NO_MEANINGFUL_PROGRESS",
                "No trusted evidence changed during the bounded interval.",
            )

        self.assertEqual(len(posted), 1)
        body = posted[0][1]
        self.assertIn("notification: `false`", body)
        self.assertIn("required_human_action: `none`", body)
        self.assertIn("self_resolution_audit", body)
        self.assertIn("workflow_run_and_job_evidence", body)
        self.assertIn("alternative_connected_paths", body)
        self.assertNotIn("press Merge", body)
        self.assertEqual(len(labels), 2)

    def test_human_only_publisher_revalidates_live_destination_and_deduplicates(self):
        fields = self.valid_fields()
        body = self.runtime.format_human_only_notice(**fields)
        marker = body.splitlines()[0]
        posted = []

        def fake_api(path):
            if path == "repos/example/foundation/pulls/62":
                return self.notice_pr()
            if path == "repos/example/foundation/issues/58":
                return {"state": "open", "user": {"login": "owner"}, "body": ""}
            raise AssertionError(path)

        with (
            patch.object(self.runtime, "api", side_effect=fake_api),
            patch.object(self.runtime, "api_list", side_effect=[[], [{"body": marker}]]),
            patch.object(self.runtime, "comment", side_effect=lambda number, text: posted.append((number, text))),
        ):
            self.runtime.human_only_notice(**fields)
            self.runtime.human_only_notice(**fields)
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0][0], 62)

    def test_human_only_publisher_rejects_stale_head_and_cross_wired_issue(self):
        fields = self.valid_fields()
        with patch.object(
            self.runtime,
            "api",
            return_value=self.notice_pr(sha="b" * 40),
        ):
            with self.assertRaises(RuntimeError):
                self.runtime.human_only_notice(**fields)
        with patch.object(
            self.runtime,
            "api",
            return_value=self.notice_pr(issue_number=59),
        ):
            with self.assertRaises(RuntimeError):
                self.runtime.human_only_notice(**fields)

    def test_no_progress_threshold_is_deterministic(self):
        now = datetime(2026, 7, 31, 13, 0, tzinfo=timezone.utc)
        self.assertEqual(
            self.runtime.minutes_without_progress("2026-07-31T12:00:00Z", now),
            60,
        )
        self.assertEqual(
            self.runtime.minutes_without_progress("2026-07-31T13:05:00Z", now),
            0,
        )


if __name__ == "__main__":
    unittest.main()

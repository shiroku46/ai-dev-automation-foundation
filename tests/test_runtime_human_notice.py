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
        self.pr = {"number": 7, "head": {"sha": self.sha}}

    def valid_notice(self, **overrides):
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
        return self.runtime.format_human_only_notice(**values)

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
            (
                "HUMAN_ONLY_CREDENTIAL_PROVIDER_UI_REQUIRED",
                ["provider/account"],
            ),
            (
                "HUMAN_ONLY_DISCONNECTED_INTEGRATION_RECONNECTION_UI_REQUIRED",
                ["provider/integration"],
            ),
        )
        for reason, targets in cases:
            with self.subTest(reason=reason):
                body = self.valid_notice(
                    reason=reason,
                    provider_ui_action=self.runtime.HUMAN_ONLY_ACTIONS[reason],
                    targets=targets,
                )
                self.assertIn(f"reason_code: `{reason}`", body)

    def test_internal_stop_is_non_notifying_audited_and_deduplicated(self):
        posted = []
        labels = []
        with (
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

    def test_human_only_publisher_deduplicates_exact_marker(self):
        body = self.valid_notice()
        marker = body.splitlines()[0]
        posted = []
        with (
            patch.object(self.runtime, "api_list", side_effect=[[], [{"body": marker}]]),
            patch.object(self.runtime, "comment", side_effect=lambda number, text: posted.append((number, text))),
        ):
            self.runtime.human_only_notice(62, body)
            self.runtime.human_only_notice(62, body)
        self.assertEqual(len(posted), 1)

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

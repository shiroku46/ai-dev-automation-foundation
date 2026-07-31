import importlib
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts.ai_recovery_supervisor import HUMAN_ACTION_BY_REASON, Reason


SHA = "a" * 40


def runtime_module():
    with patch.dict(
        os.environ,
        {
            "REPOSITORY": "example/foundation",
            "DEFAULT_BRANCH": "main",
            "AUTOMATION_OWNER": "owner",
        },
        clear=False,
    ):
        sys.modules.pop("scripts.supervisor_runtime", None)
        return importlib.import_module("scripts.supervisor_runtime")


class RuntimeHumanNoticeTest(unittest.TestCase):
    def setUp(self):
        self.runtime = runtime_module()

    def test_internal_stop_is_explicitly_non_notifying_and_audited(self):
        marker, body = self.runtime.internal_stop_body(
            pr_number=2,
            issue_number=1,
            sha=SHA,
            reason="NO_PROGRESS_MERGE_STATE",
            detail="No bounded merge transition remained.",
        )
        self.assertIn(f"NO_PROGRESS_MERGE_STATE:{SHA}", marker)
        self.assertIn("record_type: `internal_stop`", body)
        self.assertIn("notification: `none`", body)
        self.assertIn("human_action_required: `false`", body)
        for path in self.runtime.SELF_RESOLUTION_PATHS:
            self.assertIn(path, body)
        for forbidden in ("please merge", "press merge", "human action required: `true`"):
            self.assertNotIn(forbidden, body.lower())

    def test_internal_stop_rejects_invalid_sha_and_human_reason(self):
        with self.assertRaises(ValueError):
            self.runtime.internal_stop_body(
                pr_number=2,
                issue_number=1,
                sha="",
                reason="NO_PROGRESS_MERGE_STATE",
                detail="x",
            )
        with self.assertRaises(ValueError):
            self.runtime.internal_stop_body(
                pr_number=2,
                issue_number=1,
                sha=SHA,
                reason=Reason.HUMAN_CREDENTIAL_UI.value,
                detail="x",
            )

    def test_all_human_reason_families_require_canonical_ui_action(self):
        for reason in (
            Reason.HUMAN_REPOSITORY_UI,
            Reason.HUMAN_CREDENTIAL_UI,
            Reason.HUMAN_DISCONNECTED_INTEGRATION,
        ):
            with self.subTest(reason=reason):
                marker, body = self.runtime.human_only_notice_body(
                    issue_number=1,
                    pr_number=2,
                    sha=SHA,
                    reason=reason.value,
                    attempted_connected_paths=("checked callable connectors",),
                    impossibility_evidence=("no callable provider UI action exists",),
                    minimal_human_action=HUMAN_ACTION_BY_REASON[reason],
                    automatic_resume_condition="The connected state becomes visible.",
                )
                self.assertIn(reason.value, marker)
                self.assertIn("record_type: `human_only_notice`", body)
                self.assertIn("human_action_required: `true`", body)
                self.assertIn("automatic_resumption_condition", body)

    def test_human_notice_fails_closed_for_routine_or_mixed_requests(self):
        valid = HUMAN_ACTION_BY_REASON[Reason.HUMAN_CREDENTIAL_UI]
        invalid_cases = (
            "Press Merge",
            f"Approve the PR, then {valid}",
            f"Retry CI, then {valid}",
            f"{valid} Then change workflow permissions.",
        )
        for action in invalid_cases:
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.runtime.human_only_notice_body(
                    issue_number=1,
                    pr_number=2,
                    sha=SHA,
                    reason=Reason.HUMAN_CREDENTIAL_UI.value,
                    attempted_connected_paths=("checked connector",),
                    impossibility_evidence=("provider UI is required",),
                    minimal_human_action=action,
                    automatic_resume_condition="Credential becomes visible.",
                )
        with self.assertRaises(ValueError):
            self.runtime.human_only_notice_body(
                issue_number=1,
                pr_number=2,
                sha=SHA,
                reason="TRUSTED_ATTESTATION_RETRY_EXHAUSTED",
                attempted_connected_paths=("checked connector",),
                impossibility_evidence=("x",),
                minimal_human_action=valid,
                automatic_resume_condition="x",
            )

    def test_human_notice_requires_complete_exact_evidence(self):
        kwargs = {
            "issue_number": 1,
            "pr_number": 2,
            "sha": SHA,
            "reason": Reason.HUMAN_REPOSITORY_UI.value,
            "attempted_connected_paths": ("checked installation API",),
            "impossibility_evidence": ("no callable creation UI",),
            "minimal_human_action": HUMAN_ACTION_BY_REASON[Reason.HUMAN_REPOSITORY_UI],
            "automatic_resume_condition": "Repository is visible.",
        }
        for replacement in (
            {"issue_number": 0},
            {"pr_number": 0},
            {"sha": ""},
            {"attempted_connected_paths": (" ",)},
            {"impossibility_evidence": ()},
            {"automatic_resume_condition": ""},
        ):
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                self.runtime.human_only_notice_body(**{**kwargs, **replacement})

    def test_deduplication_posts_once(self):
        marker = f"<!-- foundation-stop:X:{SHA} -->"
        with patch.object(self.runtime, "api_list", return_value=[]), patch.object(
            self.runtime, "comment"
        ) as publish:
            self.assertTrue(self.runtime._deduplicated_comment(2, marker, "body"))
            publish.assert_called_once_with(2, "body")
        with patch.object(
            self.runtime,
            "api_list",
            return_value=[{"body": f"existing {marker}"}],
        ), patch.object(self.runtime, "comment") as publish:
            self.assertFalse(self.runtime._deduplicated_comment(2, marker, "body"))
            publish.assert_not_called()

    def test_no_progress_threshold_is_bounded(self):
        now = datetime(2026, 7, 31, 13, 0, tzinfo=timezone.utc)
        self.assertFalse(
            self.runtime.no_progress_elapsed(
                {"updated_at": "2026-07-31T12:00:01Z"}, now=now
            )
        )
        self.assertTrue(
            self.runtime.no_progress_elapsed(
                {"updated_at": "2026-07-31T12:00:00Z"}, now=now
            )
        )
        self.assertFalse(self.runtime.no_progress_elapsed({}, now=now))

    def test_ordinary_supervision_never_calls_human_notice_implicitly(self):
        source = Path(self.runtime.__file__).read_text(encoding="utf-8")
        self.assertEqual(source.count("post_human_only_notice("), 1)
        self.assertIn("NO_PROGRESS_AFTER_CODEX_REQUEST", source)
        self.assertIn("NO_PROGRESS_MERGE_STATE", source)


if __name__ == "__main__":
    unittest.main()

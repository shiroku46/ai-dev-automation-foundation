import importlib
import json
import os
import subprocess
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
        reason = "HUMAN_ONLY_ACCOUNT_LEVEL_REPOSITORY_CREATION_UI_UNAVAILABLE"
        values = {
            "reason": reason,
            "issue_number": 58,
            "pr_number": 62,
            "exact_head_sha": self.sha,
            "attempted_connected_paths": [
                "GitHub App get_repo for both exact targets",
                "GitHub App installation repository visibility listing",
            ],
            "impossibility_evidence": [
                "Both exact repositories are absent from the connected installation.",
                "Repository creation is unavailable through connected GitHub actions.",
            ],
            "provider_ui_action": self.runtime.HUMAN_ONLY_ACTIONS[reason],
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

    def test_formatter_accepts_only_three_human_only_families(self):
        self.assertEqual(len(self.runtime.HUMAN_ONLY_REASONS), 3)
        body = self.runtime.format_human_only_notice(**self.valid_fields())
        self.assertIn("notification: `true`", body)
        self.assertIn("issue: `#58`", body)
        self.assertIn("pull_request: `#62`", body)
        self.assertIn(self.sha, body)
        for rejected in (
            "TRUSTED_ATTESTATION_RETRY_EXHAUSTED",
            "NO_MEANINGFUL_PROGRESS",
            "MERGE_NOT_READY",
            "UNAUTHORIZED_CHANGED_PATH",
            "UNAUTHORIZED_PROTECTED_PATH",
            "AMBIGUOUS_TECHNICAL_STATE",
        ):
            with self.subTest(rejected=rejected), self.assertRaises(ValueError):
                self.runtime.format_human_only_notice(
                    **self.valid_fields(reason=rejected)
                )

    def test_formatter_fails_closed_on_incomplete_or_mismatched_fields(self):
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
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.runtime.format_human_only_notice(
                    **self.valid_fields(**overrides)
                )

    def test_self_resolution_audit_collects_real_connected_evidence(self):
        observed = []

        def fake_api(path):
            observed.append(path)
            if path == "repos/example/foundation":
                return {"visibility": "public", "default_branch": "main"}
            if path == "repos/example/foundation/pulls/7":
                return self.pr
            if path == "repos/example/foundation/collaborators/owner/permission":
                return {"permission": "admin"}
            if path == "repos/example/foundation/issues/5":
                return {
                    "state": "open",
                    "user": {"login": "owner"},
                    "body": "## Allowed paths\n- probe.py",
                }
            if "/actions/workflows/" in path:
                return {"id": len(observed), "state": "active"}
            raise AssertionError(path)

        attempts = [
            {
                "run_id": 9,
                "active": False,
                "success": False,
                "complete": True,
                "updated_at": "2026-07-31T12:00:00Z",
            }
        ]
        native = [
            {
                "workflow": "ci.yml",
                "run_id": 11,
                "status": "completed",
                "conclusion": "success",
            }
        ]
        with (
            patch.object(self.runtime, "api", side_effect=fake_api),
            patch.object(self.runtime, "changed_paths", return_value=["probe.py"]),
            patch.object(self.runtime, "attestation_attempts", return_value=attempts),
            patch.object(
                self.runtime, "native_workflow_evidence", return_value=(True, native)
            ),
            patch.object(
                self.runtime,
                "_sanitized_check_evidence",
                return_value='[{"name":"CI"}]',
            ),
            patch.object(
                self.runtime,
                "exact_codex_evidence",
                return_value={
                    "state": "blocking",
                    "timestamp": "2026-07-31T12:10:00Z",
                    "request_timestamp": "2026-07-31T12:00:00Z",
                },
            ),
            patch.object(self.runtime, "unresolved_review_threads", return_value=True),
        ):
            audit = self.runtime.self_resolution_audit(
                self.pr, 5, "BLOCKING_CODEX_REVIEW"
            )

        self.assertGreaterEqual(observed.count("repos/example/foundation/pulls/7"), 2)
        self.assertIn("repos/example/foundation", observed)
        self.assertIn("repos/example/foundation/issues/5", observed)
        self.assertIn(
            "repos/example/foundation/collaborators/owner/permission", observed
        )
        self.assertIn("run_id", audit["workflow_run_and_job_evidence"])
        self.assertIn("ci.yml", audit["native_pull_request_workflow_evidence"])
        self.assertIn("CI", audit["check_evidence"])
        self.assertIn("codex=blocking", audit["review_and_provenance"])
        self.assertIn("mergeable=True", audit["mergeability"])
        self.assertIn("all_paths=True", audit["scope_and_authorization"])
        self.assertIn("initial_and_final_head_confirmed=true", audit["repository_metadata"])

    def test_failed_or_moved_audit_persists_nothing(self):
        moved = {**self.pr, "head": {"sha": "b" * 40}}
        with (
            patch.object(
                self.runtime,
                "api",
                side_effect=[
                    {"visibility": "public", "default_branch": "main"},
                    self.pr,
                    {"permission": "admin"},
                    {"id": 1, "state": "active"},
                    {"id": 2, "state": "active"},
                    {"id": 3, "state": "active"},
                    {"id": 4, "state": "active"},
                    {
                        "state": "open",
                        "user": {"login": "owner"},
                        "body": "## Allowed paths\n- probe.py",
                    },
                    moved,
                ],
            ),
            patch.object(self.runtime, "changed_paths", return_value=["probe.py"]),
            patch.object(self.runtime, "attestation_attempts", return_value=[]),
            patch.object(
                self.runtime, "native_workflow_evidence", return_value=(False, [])
            ),
            patch.object(self.runtime, "_sanitized_check_evidence", return_value="[]"),
            patch.object(
                self.runtime,
                "exact_codex_evidence",
                return_value={
                    "state": "pending",
                    "timestamp": None,
                    "request_timestamp": None,
                },
            ),
            patch.object(self.runtime, "unresolved_review_threads", return_value=False),
            patch.object(self.runtime, "persist_internal_stop_record") as persist,
        ):
            with self.assertRaises(RuntimeError):
                self.runtime.stop_report(
                    self.pr, 5, "NO_MEANINGFUL_PROGRESS", "bounded stop"
                )
        persist.assert_not_called()

    def test_internal_record_paths_and_content_are_deterministic(self):
        path = self.runtime.internal_stop_record_path(
            7, self.sha, "NO_MEANINGFUL_PROGRESS"
        )
        self.assertEqual(
            path,
            f"automation-stops/pr-7/{self.sha}/NO_MEANINGFUL_PROGRESS.json",
        )
        notice_path = self.runtime.human_notice_record_path(
            7,
            self.sha,
            "HUMAN_ONLY_CREDENTIAL_PROVIDER_UI_REQUIRED",
        )
        self.assertTrue(notice_path.endswith(".notice.json"))
        first = self.runtime.canonical_internal_stop_record(
            pr_number=7,
            issue_number=5,
            sha=self.sha,
            reason="NO_MEANINGFUL_PROGRESS",
            detail="bounded",
            audit={"z": "2", "a": "1"},
        )
        second = self.runtime.canonical_internal_stop_record(
            pr_number=7,
            issue_number=5,
            sha=self.sha,
            reason="NO_MEANINGFUL_PROGRESS",
            detail="bounded",
            audit={"a": "1", "z": "2"},
        )
        self.assertEqual(first, second)
        payload = json.loads(first)
        self.assertFalse(payload["notification"])
        self.assertIsNone(payload["required_human_action"])

    def test_persist_exact_record_requires_content_equality(self):
        path = self.runtime.internal_stop_record_path(
            7, self.sha, "NO_MEANINGFUL_PROGRESS"
        )
        content = self.runtime.canonical_internal_stop_record(
            pr_number=7,
            issue_number=5,
            sha=self.sha,
            reason="NO_MEANINGFUL_PROGRESS",
            detail="bounded",
            audit={"a": "1"},
        )
        success = subprocess.CompletedProcess(["gh"], 0, "{}", "")
        with (
            patch.object(self.runtime, "ensure_internal_stop_branch"),
            patch.object(self.runtime, "_existing_internal_record", return_value=None),
            patch.object(self.runtime, "gh_result", return_value=success) as put,
        ):
            self.assertTrue(
                self.runtime.persist_internal_stop_record(
                    path, content, "NO_MEANINGFUL_PROGRESS", 7
                )
            )
        self.assertEqual(put.call_count, 1)
        self.assertIn("PUT", put.call_args.args)

        with (
            patch.object(self.runtime, "ensure_internal_stop_branch"),
            patch.object(self.runtime, "_existing_internal_record", return_value="{}\n"),
        ):
            with self.assertRaises(RuntimeError):
                self.runtime.persist_internal_stop_record(
                    path, content, "NO_MEANINGFUL_PROGRESS", 7
                )

    def test_stop_report_persists_without_comment_or_label_mutation(self):
        path = self.runtime.internal_stop_record_path(
            7, self.sha, "NO_MEANINGFUL_PROGRESS"
        )
        with (
            patch.object(self.runtime, "self_resolution_audit", return_value={"a": "1"}),
            patch.object(self.runtime, "_live_pr", return_value=self.pr),
            patch.object(
                self.runtime, "persist_internal_stop_record", return_value=True
            ) as persist,
            patch.object(self.runtime, "ensure_label") as label,
            patch.object(self.runtime, "comment") as comment,
        ):
            self.runtime.stop_report(
                self.pr, 5, "NO_MEANINGFUL_PROGRESS", "bounded stop"
            )
        comment.assert_not_called()
        label.assert_not_called()
        self.assertEqual(persist.call_args.args[0], path)
        self.assertIn('"notification": false', persist.call_args.args[1])

    def test_codex_events_are_sorted_by_immutable_event_time(self):
        older_review = {
            "body": f"No major issues. {self.sha}",
            "user": {"login": self.runtime.CODEX_LOGIN},
            "submitted_at": "2026-07-31T11:00:00Z",
        }
        newer_blocker = {
            "body": f"Blocking finding for {self.sha}",
            "user": {"login": self.runtime.CODEX_LOGIN},
            "created_at": "2026-07-31T12:00:00Z",
            "updated_at": "2026-07-31T12:00:00Z",
        }
        with (
            patch.object(
                self.runtime,
                "api_list",
                side_effect=[[newer_blocker], [older_review]],
            ),
            patch.object(self.runtime, "unresolved_review_threads", return_value=False),
        ):
            evidence = self.runtime.exact_codex_evidence(7, self.sha)
        self.assertEqual(evidence["state"], "blocking")
        self.assertEqual(evidence["timestamp"], "2026-07-31T12:00:00Z")

    def test_human_notice_persists_exact_record_before_comment_and_dedupes(self):
        fields = self.valid_fields()
        marker = self.runtime.format_human_only_notice(**fields).splitlines()[0]
        live = self.notice_pr()
        audit = {"exact_head_sha": self.sha}
        posted = []
        records = {}
        untrusted = {
            "body": marker,
            "user": {"login": "contributor"},
            "created_at": "2026-07-31T12:00:00Z",
            "updated_at": "2026-07-31T12:00:00Z",
        }

        def persist(path, content, reason, number):
            records[path] = content
            return True

        def existing(path):
            return records.get(path)

        with (
            patch.object(self.runtime, "_validated_notice_destination", return_value=live),
            patch.object(self.runtime, "self_resolution_audit", return_value=audit),
            patch.object(self.runtime, "api_list", return_value=[untrusted]),
            patch.object(
                self.runtime, "persist_human_notice_record", side_effect=persist
            ) as persisted,
            patch.object(
                self.runtime, "_existing_internal_record", side_effect=existing
            ),
            patch.object(
                self.runtime,
                "comment",
                side_effect=lambda number, body: posted.append((number, body)),
            ),
        ):
            self.runtime.human_only_notice(**fields)
        self.assertEqual(len(posted), 1)
        persisted.assert_called_once()
        record = json.loads(next(iter(records.values())))
        self.assertEqual(record["attempted_connected_paths"], fields["attempted_connected_paths"])
        self.assertEqual(record["impossibility_evidence"], fields["impossibility_evidence"])
        self.assertEqual(record["targets"], fields["targets"])

        trusted = {
            "body": posted[0][1],
            "user": {"login": self.runtime.ACTIONS_LOGIN},
            "created_at": "2026-07-31T12:00:00Z",
            "updated_at": "2026-07-31T12:00:00Z",
        }
        with (
            patch.object(self.runtime, "_validated_notice_destination", return_value=live),
            patch.object(self.runtime, "self_resolution_audit", return_value=audit),
            patch.object(self.runtime, "api_list", return_value=[trusted]),
            patch.object(self.runtime, "persist_human_notice_record", side_effect=persist),
            patch.object(
                self.runtime, "_existing_internal_record", side_effect=existing
            ),
            patch.object(self.runtime, "comment") as no_comment,
        ):
            self.runtime.human_only_notice(**fields)
        no_comment.assert_not_called()

    def test_trusted_notice_comment_without_matching_record_fails_closed(self):
        fields = self.valid_fields()
        live = self.notice_pr()
        marker = self.runtime.format_human_only_notice(**fields).splitlines()[0]
        trusted = {
            "body": marker,
            "user": {"login": self.runtime.ACTIONS_LOGIN},
            "created_at": "2026-07-31T12:00:00Z",
            "updated_at": "2026-07-31T12:00:00Z",
        }
        with (
            patch.object(self.runtime, "_validated_notice_destination", return_value=live),
            patch.object(
                self.runtime,
                "self_resolution_audit",
                return_value={"exact_head_sha": self.sha},
            ),
            patch.object(self.runtime, "api_list", return_value=[trusted]),
            patch.object(self.runtime, "persist_human_notice_record", return_value=False),
            patch.object(self.runtime, "_existing_internal_record", return_value=None),
        ):
            with self.assertRaises(RuntimeError):
                self.runtime.human_only_notice(**fields)

    def test_human_notice_rejects_stale_head_and_cross_wired_issue(self):
        fields = self.valid_fields()
        with patch.object(
            self.runtime, "api", return_value=self.notice_pr(sha="b" * 40)
        ):
            with self.assertRaises(RuntimeError):
                self.runtime.human_only_notice(**fields)
        with patch.object(
            self.runtime, "api", return_value=self.notice_pr(issue_number=59)
        ):
            with self.assertRaises(RuntimeError):
                self.runtime.human_only_notice(**fields)

    def test_timestamp_helpers_are_deterministic(self):
        now = datetime(2026, 7, 31, 13, 0, tzinfo=timezone.utc)
        self.assertEqual(
            self.runtime.minutes_since("2026-07-31T12:00:00Z", now), 60
        )
        attempts = [
            {"success": True, "updated_at": "2026-07-31T12:00:00Z"},
            {"success": True, "updated_at": "2026-07-31T12:30:00Z"},
            {"success": False, "updated_at": "2026-07-31T12:50:00Z"},
        ]
        self.assertEqual(
            self.runtime.latest_successful_attestation_timestamp(attempts),
            "2026-07-31T12:30:00Z",
        )
        self.assertEqual(
            self.runtime._evidence_anchor(
                "2026-07-31T12:30:00Z", "2026-07-31T12:45:00Z"
            ),
            "2026-07-31T12:45:00Z",
        )


if __name__ == "__main__":
    unittest.main()

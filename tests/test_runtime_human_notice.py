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
        self.targets = (
            "shiroku46/ai-dev-automation-foundation",
            "shiroku46/ai-dev-automation-foundation-e2e",
        )
        self.attempted = tuple(
            f"GitHub API GET repos/{target}" for target in self.targets
        )
        self.impossible = tuple(
            f"GitHub API returned HTTP 404 for {target}; the exact repository is absent or unavailable to the connected token."
            for target in self.targets
        )
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
            "attempted_connected_paths": self.attempted,
            "impossibility_evidence": self.impossible,
            "provider_ui_action": self.runtime.HUMAN_ONLY_ACTIONS[reason],
            "automatic_resume_condition": (
                "Both exact repositories exist and are visible to the connected GitHub App."
            ),
            "targets": self.targets,
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

    def audit_api(self, path):
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
            return {"id": 1, "state": "active"}
        raise AssertionError(path)

    def audit_dependencies(self):
        return (
            patch.object(self.runtime, "api", side_effect=self.audit_api),
            patch.object(self.runtime, "changed_paths", return_value=["probe.py"]),
            patch.object(self.runtime, "attestation_attempts", return_value=[]),
            patch.object(
                self.runtime, "native_workflow_evidence", return_value=(True, [])
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
        )

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

    def test_formatter_fails_closed_on_incomplete_fields(self):
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

    def test_repository_creation_evidence_is_derived_from_connected_queries(self):
        missing = subprocess.CompletedProcess(["gh"], 1, "", "HTTP 404 Not Found")
        with patch.object(self.runtime, "gh_result", side_effect=[missing, missing]):
            attempted, impossible = self.runtime._connected_repository_creation_evidence(
                self.targets
            )
        self.assertEqual(attempted, self.attempted)
        self.assertEqual(impossible, self.impossible)

        visible = [
            subprocess.CompletedProcess(
                ["gh"], 0, json.dumps({"full_name": target}), ""
            )
            for target in self.targets
        ]
        with patch.object(self.runtime, "gh_result", side_effect=visible):
            with self.assertRaises(RuntimeError):
                self.runtime._connected_repository_creation_evidence(self.targets)

    def test_provider_reasons_fail_closed_without_connected_adapter(self):
        for reason in (
            "HUMAN_ONLY_CREDENTIAL_PROVIDER_UI_REQUIRED",
            "HUMAN_ONLY_DISCONNECTED_INTEGRATION_RECONNECTION_UI_REQUIRED",
        ):
            with self.subTest(reason=reason), self.assertRaises(RuntimeError):
                self.runtime._connected_human_notice_evidence(reason, ("provider",))

    def test_internal_audit_collects_exact_head_evidence(self):
        patches = self.audit_dependencies()
        with patches[0], patches[1], patches[2], patches[3] as native, patches[4], patches[5], patches[6]:
            audit = self.runtime.self_resolution_audit(
                self.pr, 5, "BLOCKING_CODEX_REVIEW"
            )
        native.assert_called_once_with(self.sha, 7)
        self.assertIn("initial_and_final_head_confirmed=true", audit["repository_metadata"])
        self.assertIn("all_paths=True", audit["scope_and_authorization"])
        self.assertEqual(audit["human_only_connected_evidence"], "not-applicable")

    def test_final_human_audit_rederives_and_binds_connected_condition(self):
        reason = "HUMAN_ONLY_ACCOUNT_LEVEL_REPOSITORY_CREATION_UI_UNAVAILABLE"
        context = {
            "targets": self.targets,
            "attempted": self.attempted,
            "impossible": self.impossible,
        }
        patches = self.audit_dependencies()
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
            patch.object(
                self.runtime,
                "_connected_human_notice_evidence",
                return_value=(self.attempted, self.impossible),
            ) as connected,
        ):
            audit = self.runtime.self_resolution_audit(
                self.pr,
                5,
                reason,
                human_notice_context=context,
            )
        connected.assert_called_once_with(reason, self.targets)
        bound = json.loads(audit["human_only_connected_evidence"])
        self.assertEqual(bound["targets"], list(self.targets))
        self.assertEqual(bound["attempted_connected_paths"], list(self.attempted))
        self.assertEqual(bound["impossibility_evidence"], list(self.impossible))

    def test_repository_becoming_visible_during_final_audit_fails_closed(self):
        reason = "HUMAN_ONLY_ACCOUNT_LEVEL_REPOSITORY_CREATION_UI_UNAVAILABLE"
        context = {
            "targets": self.targets,
            "attempted": self.attempted,
            "impossible": self.impossible,
        }
        patches = self.audit_dependencies()
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
            patch.object(
                self.runtime,
                "_connected_human_notice_evidence",
                return_value=(self.attempted, ()),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "impossibility evidence changed"):
                self.runtime.self_resolution_audit(
                    self.pr,
                    5,
                    reason,
                    human_notice_context=context,
                )

    def test_failed_or_moved_internal_audit_persists_nothing(self):
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

    def test_stop_report_persists_without_comment_or_label_mutation(self):
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
        self.assertIn('"notification": false', persist.call_args.args[1])

    def test_human_notice_persists_exact_record_then_rechecks_condition(self):
        fields = self.valid_fields()
        live = self.notice_pr()
        records = {}
        posted = []

        def persist(path, content, reason, number):
            records[path] = content
            return True

        with (
            patch.object(
                self.runtime,
                "_connected_human_notice_evidence",
                return_value=(self.attempted, self.impossible),
            ) as connected,
            patch.object(self.runtime, "_validated_notice_destination", return_value=live),
            patch.object(
                self.runtime,
                "self_resolution_audit",
                return_value={"human_only_connected_evidence": "bound"},
            ) as audit,
            patch.object(self.runtime, "api_list", return_value=[]),
            patch.object(
                self.runtime, "persist_human_notice_record", side_effect=persist
            ),
            patch.object(
                self.runtime,
                "_existing_internal_record",
                side_effect=lambda path: records.get(path),
            ),
            patch.object(
                self.runtime,
                "comment",
                side_effect=lambda number, body: posted.append((number, body)),
            ),
        ):
            self.runtime.human_only_notice(**fields)
        self.assertGreaterEqual(connected.call_count, 3)
        audit.assert_called_once()
        context = audit.call_args.kwargs["human_notice_context"]
        self.assertEqual(context["targets"], self.targets)
        self.assertEqual(context["attempted"], self.attempted)
        self.assertEqual(context["impossible"], self.impossible)
        self.assertEqual(len(posted), 1)
        record = json.loads(next(iter(records.values())))
        self.assertEqual(record["attempted_connected_paths"], list(self.attempted))

    def test_repository_becoming_visible_after_final_audit_prevents_publication(self):
        fields = self.valid_fields()
        live = self.notice_pr()
        records = {}

        def persist(path, content, reason, number):
            records[path] = content
            return True

        with (
            patch.object(
                self.runtime,
                "_connected_human_notice_evidence",
                side_effect=[
                    (self.attempted, self.impossible),
                    (self.attempted, ()),
                ],
            ),
            patch.object(self.runtime, "_validated_notice_destination", return_value=live),
            patch.object(
                self.runtime,
                "self_resolution_audit",
                return_value={"human_only_connected_evidence": "bound"},
            ),
            patch.object(
                self.runtime, "persist_human_notice_record", side_effect=persist
            ),
            patch.object(self.runtime, "comment") as comment,
        ):
            with self.assertRaisesRegex(RuntimeError, "changed after the final audit"):
                self.runtime.human_only_notice(**fields)
        comment.assert_not_called()

    def test_trusted_notice_deduplication_requires_matching_record(self):
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
            patch.object(
                self.runtime,
                "_connected_human_notice_evidence",
                return_value=(self.attempted, self.impossible),
            ),
            patch.object(self.runtime, "_validated_notice_destination", return_value=live),
            patch.object(
                self.runtime,
                "self_resolution_audit",
                return_value={"human_only_connected_evidence": "bound"},
            ),
            patch.object(self.runtime, "api_list", return_value=[trusted]),
            patch.object(self.runtime, "persist_human_notice_record", return_value=False),
            patch.object(self.runtime, "_existing_internal_record", return_value=None),
        ):
            with self.assertRaises(RuntimeError):
                self.runtime.human_only_notice(**fields)

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

    def test_records_and_timestamp_helpers_are_deterministic(self):
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
        self.assertFalse(json.loads(first)["notification"])
        now = datetime(2026, 7, 31, 13, 0, tzinfo=timezone.utc)
        self.assertEqual(
            self.runtime.minutes_since("2026-07-31T12:00:00Z", now), 60
        )


if __name__ == "__main__":
    unittest.main()

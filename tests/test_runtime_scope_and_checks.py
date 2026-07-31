import importlib
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from scripts.supervisor_policy import (
    declared_paths,
    protected_scope_is_authorized,
    scope_is_authorized,
)

ENVIRONMENT = {
    "REPOSITORY": "example/foundation",
    "DEFAULT_BRANCH": "main",
    "AUTOMATION_OWNER": "owner",
}
SHA = "a" * 40
DEFAULT_SHA = "b" * 40
PR_NUMBER = 7


def load_runtime():
    with patch.dict(os.environ, ENVIRONMENT, clear=False):
        sys.modules.pop("scripts.supervisor_runtime", None)
        return importlib.import_module("scripts.supervisor_runtime")


class SourceScopePolicyTest(unittest.TestCase):
    def test_all_changed_and_renamed_paths_must_match_issue_allowlist(self):
        body = """
## Allowed scope
- scripts/probe.py
- tests/**

<!-- foundation-protected-authorization
paths:
- scripts/supervisor_runtime.py
operation: bounded
-->
"""
        self.assertEqual(declared_paths(body), {"scripts/probe.py", "tests/**"})
        self.assertTrue(
            scope_is_authorized(
                ["scripts/probe.py", "tests/unit/test_probe.py"], body
            )
        )
        self.assertFalse(
            scope_is_authorized(["scripts/supervisor_runtime.py"], body)
        )
        self.assertFalse(
            scope_is_authorized(["scripts/probe.py", "README.md"], body)
        )

    def test_protected_paths_require_independent_declarations(self):
        protected_only = """
<!-- foundation-protected-authorization
paths:
- scripts/supervisor_runtime.py
operation: bounded
-->
"""
        self.assertFalse(
            scope_is_authorized(["scripts/supervisor_runtime.py"], protected_only)
        )
        body = """
## Allowed paths
- scripts/supervisor_runtime.py

<!-- foundation-protected-authorization
paths:
- scripts/supervisor_runtime.py
operation: bounded
-->
"""
        self.assertTrue(scope_is_authorized(["scripts/supervisor_runtime.py"], body))
        self.assertTrue(
            protected_scope_is_authorized(["scripts/supervisor_runtime.py"], body)
        )

    def test_invalid_or_unbounded_path_declarations_fail_closed(self):
        body = "## Allowed paths\n- ../outside.py\n- *.py\n- prose description here\n"
        self.assertEqual(declared_paths(body), {"*.py"})
        self.assertFalse(scope_is_authorized(["README.md"], body))
        self.assertFalse(scope_is_authorized(["probe.py"], body))


class NativeWorkflowEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.runtime = load_runtime()

    def metadata_result(self, filename, workflow_id):
        return subprocess.CompletedProcess(
            ["gh"],
            0,
            json.dumps(
                {
                    "id": workflow_id,
                    "state": "active",
                    "path": f".github/workflows/{filename}",
                }
            ),
            "",
        )

    def make_run(
        self, workflow_id, *, status="completed", conclusion="success", sha=SHA
    ):
        return {
            "id": workflow_id * 100,
            "workflow_id": workflow_id,
            "event": "pull_request",
            "head_sha": sha,
            "repository": {"full_name": "example/foundation"},
            "head_repository": {"full_name": "example/foundation"},
            "pull_requests": [{"number": PR_NUMBER, "base": {"ref": "main"}}],
            "path": {
                1: ".github/workflows/ci.yml",
                2: ".github/workflows/unit-tests.yml",
                3: ".github/workflows/e2e.yml",
            }[workflow_id],
            "status": status,
            "conclusion": conclusion,
            "run_number": 4,
            "run_attempt": 1,
            "updated_at": "2026-07-31T12:00:00Z",
        }

    def evidence(
        self,
        metadata,
        runs,
        definitions=None,
        default_shas=(DEFAULT_SHA, DEFAULT_SHA),
    ):
        definitions = definitions or [True] * len(metadata)
        with (
            patch.object(self.runtime, "gh_result", side_effect=metadata),
            patch.object(
                self.runtime,
                "_workflow_definition_matches_default",
                side_effect=definitions,
            ),
            patch.object(self.runtime, "api_key_pages", side_effect=runs),
            patch.object(
                self.runtime, "current_default_sha", side_effect=default_shas
            ),
        ):
            return self.runtime.native_workflow_evidence(SHA, PR_NUMBER)

    def test_complete_fixed_native_workflows_authorize_exact_sha(self):
        clean, evidence = self.evidence(
            [
                self.metadata_result("ci.yml", 1),
                self.metadata_result("unit-tests.yml", 2),
                self.metadata_result("e2e.yml", 3),
            ],
            [[self.make_run(1)], [self.make_run(2)], [self.make_run(3)]],
        )
        self.assertTrue(clean)
        self.assertEqual(
            [item["workflow"] for item in evidence],
            ["ci.yml", "unit-tests.yml", "e2e.yml"],
        )

    def test_candidate_modified_workflow_definition_fails_closed(self):
        clean, evidence = self.evidence(
            [
                self.metadata_result("ci.yml", 1),
                self.metadata_result("unit-tests.yml", 2),
                subprocess.CompletedProcess(["gh"], 1, "", "HTTP 404 Not Found"),
            ],
            [[self.make_run(2)]],
            definitions=[False, True],
        )
        self.assertFalse(clean)
        self.assertEqual(evidence[0]["status"], "untrusted-definition")

    def test_definition_binding_compares_candidate_to_one_stable_default_blob(self):
        with patch.object(
            self.runtime,
            "_content_blob_sha",
            side_effect=["blob-1", "blob-1"],
        ) as blobs:
            self.assertTrue(
                self.runtime._workflow_definition_matches_default(
                    "ci.yml", SHA, DEFAULT_SHA
                )
            )
        self.assertEqual(blobs.call_args_list[0].args[1], DEFAULT_SHA)
        self.assertEqual(blobs.call_count, 2)

        with patch.object(
            self.runtime,
            "_content_blob_sha",
            side_effect=["default", "candidate"],
        ):
            self.assertFalse(
                self.runtime._workflow_definition_matches_default(
                    "ci.yml", SHA, DEFAULT_SHA
                )
            )

    def test_default_branch_move_during_complete_gate_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "Default branch moved"):
            self.evidence(
                [
                    self.metadata_result("ci.yml", 1),
                    self.metadata_result("unit-tests.yml", 2),
                    subprocess.CompletedProcess(
                        ["gh"], 1, "", "HTTP 404 Not Found"
                    ),
                ],
                [[self.make_run(1)], [self.make_run(2)]],
                default_shas=(DEFAULT_SHA, "c" * 40),
            )

    def test_every_definition_uses_the_same_stable_default_sha(self):
        with (
            patch.object(
                self.runtime,
                "gh_result",
                side_effect=[
                    self.metadata_result("ci.yml", 1),
                    self.metadata_result("unit-tests.yml", 2),
                    self.metadata_result("e2e.yml", 3),
                ],
            ),
            patch.object(
                self.runtime,
                "_workflow_definition_matches_default",
                return_value=True,
            ) as definitions,
            patch.object(
                self.runtime,
                "api_key_pages",
                side_effect=[
                    [self.make_run(1)],
                    [self.make_run(2)],
                    [self.make_run(3)],
                ],
            ),
            patch.object(
                self.runtime,
                "current_default_sha",
                side_effect=[DEFAULT_SHA, DEFAULT_SHA],
            ),
        ):
            clean, _ = self.runtime.native_workflow_evidence(SHA, PR_NUMBER)
        self.assertTrue(clean)
        self.assertEqual(
            [call.args[2] for call in definitions.call_args_list],
            [DEFAULT_SHA, DEFAULT_SHA, DEFAULT_SHA],
        )

    def test_missing_pending_failed_stale_and_cross_pr_runs_fail_closed(self):
        not_found = subprocess.CompletedProcess(["gh"], 1, "", "HTTP 404 Not Found")
        cross_pr = self.make_run(1)
        cross_pr["pull_requests"] = [{"number": 99, "base": {"ref": "main"}}]
        cases = (
            ([], [self.make_run(2)]),
            (
                [self.make_run(1, status="in_progress", conclusion=None)],
                [self.make_run(2)],
            ),
            ([self.make_run(1, conclusion="failure")], [self.make_run(2)]),
            ([self.make_run(1, sha="b" * 40)], [self.make_run(2)]),
            ([cross_pr], [self.make_run(2)]),
        )
        for ci_runs, unit_runs in cases:
            with self.subTest(ci_runs=ci_runs):
                clean, _ = self.evidence(
                    [
                        self.metadata_result("ci.yml", 1),
                        self.metadata_result("unit-tests.yml", 2),
                        not_found,
                    ],
                    [ci_runs, unit_runs],
                )
                self.assertFalse(clean)

    def test_wrong_repository_or_workflow_identity_is_rejected(self):
        wrong_repo = self.make_run(1)
        wrong_repo["repository"] = {"full_name": "attacker/fork"}
        wrong_workflow = self.make_run(2)
        wrong_workflow["workflow_id"] = 99
        clean, evidence = self.evidence(
            [
                self.metadata_result("ci.yml", 1),
                self.metadata_result("unit-tests.yml", 2),
                subprocess.CompletedProcess(["gh"], 1, "", "HTTP 404 Not Found"),
            ],
            [[wrong_repo], [wrong_workflow]],
        )
        self.assertFalse(clean)
        self.assertTrue(all(item["status"] == "missing" for item in evidence))

    def test_optional_e2e_is_not_required_when_absent(self):
        clean, evidence = self.evidence(
            [
                self.metadata_result("ci.yml", 1),
                self.metadata_result("unit-tests.yml", 2),
                subprocess.CompletedProcess(["gh"], 1, "", "HTTP 404 Not Found"),
            ],
            [[self.make_run(1)], [self.make_run(2)]],
        )
        self.assertTrue(clean)
        self.assertEqual(len(evidence), 2)


class StopMutationRevalidationTest(unittest.TestCase):
    def setUp(self):
        self.runtime = load_runtime()
        self.candidate = {
            "number": PR_NUMBER,
            "head": {"sha": SHA},
            "mergeable": True,
        }

    def test_cleared_authorization_reason_fails_closed(self):
        with (
            patch.object(self.runtime, "_live_pr", return_value=self.candidate),
            patch.object(
                self.runtime,
                "source_and_scope",
                return_value=(9, {"number": 9}, ["docs/probe.md"], None),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "no longer supported"):
                self.runtime._revalidate_stop_reason(
                    PR_NUMBER, SHA, 9, "UNAUTHORIZED_CHANGED_PATH"
                )

    def test_stop_report_revalidates_before_persist_and_close(self):
        with (
            patch.object(self.runtime, "self_resolution_audit", return_value={}),
            patch.object(
                self.runtime,
                "_revalidate_stop_reason",
                return_value=self.candidate,
            ) as revalidate,
            patch.object(
                self.runtime,
                "canonical_internal_stop_record",
                return_value="{}\n",
            ),
            patch.object(self.runtime, "persist_internal_stop_record") as persist,
            patch.object(self.runtime, "gh") as gh,
        ):
            self.runtime.stop_report(
                self.candidate,
                9,
                "UNAUTHORIZED_CHANGED_PATH",
                "blocked",
                close=True,
            )
        self.assertEqual(revalidate.call_count, 3)
        persist.assert_called_once()
        gh.assert_called_once_with(
            "pr", "close", str(PR_NUMBER), "--repo", "example/foundation"
        )


class CompleteStopReasonRevalidationTest(unittest.TestCase):
    def setUp(self):
        self.runtime = load_runtime()
        self.candidate = {
            "number": PR_NUMBER,
            "state": "open",
            "head": {"sha": SHA},
            "mergeable": True,
            "mergeable_state": "clean",
        }
        self.clean_scope = (9, {"number": 9}, ["docs/probe.md"], None)
        self.successful_attempt = [
            {
                "success": True,
                "active": False,
                "run_id": 1,
                "updated_at": "2026-07-31T12:00:00Z",
            }
        ]

    def test_each_source_scope_reason_must_still_match(self):
        reasons = (
            "MISSING_TRUSTED_SOURCE_ISSUE",
            "UNTRUSTED_SOURCE_ISSUE",
            "INCOMPLETE_CHANGED_FILE_EVIDENCE",
            "UNAUTHORIZED_CHANGED_PATH",
            "UNAUTHORIZED_PROTECTED_PATH",
        )
        for reason in reasons:
            with (
                self.subTest(reason=reason),
                patch.object(self.runtime, "_live_pr", return_value=self.candidate),
                patch.object(
                    self.runtime,
                    "source_and_scope",
                    return_value=self.clean_scope,
                ),
                self.assertRaisesRegex(RuntimeError, "no longer supported"),
            ):
                self.runtime._revalidate_stop_reason(PR_NUMBER, SHA, 9, reason)

    def test_non_scope_reason_rejects_changed_source_issue(self):
        changed_source = (10, {"number": 10}, ["docs/probe.md"], None)
        with (
            patch.object(self.runtime, "_live_pr", return_value=self.candidate),
            patch.object(
                self.runtime,
                "source_and_scope",
                return_value=changed_source,
            ),
            self.assertRaisesRegex(RuntimeError, "source/scope evidence changed"),
        ):
            self.runtime._revalidate_stop_reason(
                PR_NUMBER,
                SHA,
                9,
                "TRUSTED_ATTESTATION_RETRY_EXHAUSTED",
            )

    def test_retry_exhaustion_cleared_by_success_fails_closed(self):
        with (
            patch.object(self.runtime, "_live_pr", return_value=self.candidate),
            patch.object(self.runtime, "source_and_scope", return_value=self.clean_scope),
            patch.object(
                self.runtime,
                "attestation_attempts",
                return_value=self.successful_attempt,
            ),
            self.assertRaisesRegex(RuntimeError, "no longer supported"),
        ):
            self.runtime._revalidate_stop_reason(
                PR_NUMBER, SHA, 9, "TRUSTED_ATTESTATION_RETRY_EXHAUSTED"
            )

    def test_current_retry_exhaustion_remains_supported(self):
        exhausted = [
            {"success": False, "active": False, "run_id": number}
            for number in (1, 2, 3)
        ]
        with (
            patch.object(self.runtime, "_live_pr", return_value=self.candidate),
            patch.object(self.runtime, "source_and_scope", return_value=self.clean_scope),
            patch.object(self.runtime, "attestation_attempts", return_value=exhausted),
        ):
            live = self.runtime._revalidate_stop_reason(
                PR_NUMBER, SHA, 9, "TRUSTED_ATTESTATION_RETRY_EXHAUSTED"
            )
        self.assertIs(live, self.candidate)

    def test_no_progress_cleared_by_fresh_native_evidence_fails_closed(self):
        native = [
            {
                "updated_at": "2026-07-31T12:00:00Z",
                "status": "in_progress",
                "conclusion": None,
            }
        ]
        with (
            patch.object(self.runtime, "_live_pr", return_value=self.candidate),
            patch.object(self.runtime, "source_and_scope", return_value=self.clean_scope),
            patch.object(
                self.runtime,
                "attestation_attempts",
                return_value=self.successful_attempt,
            ),
            patch.object(
                self.runtime,
                "native_workflow_evidence",
                return_value=(False, native),
            ),
            patch.object(self.runtime, "minutes_since", return_value=1),
            self.assertRaisesRegex(RuntimeError, "no longer supported"),
        ):
            self.runtime._revalidate_stop_reason(
                PR_NUMBER, SHA, 9, "NO_MEANINGFUL_PROGRESS"
            )

    def test_blocking_codex_reason_cleared_by_clean_review_fails_closed(self):
        with (
            patch.object(self.runtime, "_live_pr", return_value=self.candidate),
            patch.object(self.runtime, "source_and_scope", return_value=self.clean_scope),
            patch.object(
                self.runtime,
                "attestation_attempts",
                return_value=self.successful_attempt,
            ),
            patch.object(
                self.runtime,
                "native_workflow_evidence",
                return_value=(True, []),
            ),
            patch.object(
                self.runtime,
                "exact_codex_evidence",
                return_value={"state": "clean", "timestamp": None, "request_timestamp": None},
            ),
            self.assertRaisesRegex(RuntimeError, "no longer supported"),
        ):
            self.runtime._revalidate_stop_reason(
                PR_NUMBER, SHA, 9, "BLOCKING_CODEX_REVIEW"
            )

    def test_merge_not_ready_cleared_by_mergeable_candidate_fails_closed(self):
        with (
            patch.object(self.runtime, "_live_pr", return_value=self.candidate),
            patch.object(self.runtime, "source_and_scope", return_value=self.clean_scope),
            patch.object(
                self.runtime,
                "attestation_attempts",
                return_value=self.successful_attempt,
            ),
            patch.object(
                self.runtime,
                "native_workflow_evidence",
                return_value=(True, []),
            ),
            patch.object(
                self.runtime,
                "exact_codex_evidence",
                return_value={"state": "clean", "timestamp": None, "request_timestamp": None},
            ),
            self.assertRaisesRegex(RuntimeError, "no longer supported"),
        ):
            self.runtime._revalidate_stop_reason(PR_NUMBER, SHA, 9, "MERGE_NOT_READY")

    def test_reasons_without_current_derivation_fail_closed(self):
        for reason in ("UNTRUSTED_EVIDENCE", "AMBIGUOUS_TECHNICAL_STATE"):
            with (
                self.subTest(reason=reason),
                patch.object(self.runtime, "_live_pr", return_value=self.candidate),
                patch.object(
                    self.runtime,
                    "source_and_scope",
                    return_value=self.clean_scope,
                ),
                patch.object(
                    self.runtime,
                    "attestation_attempts",
                    return_value=self.successful_attempt,
                ),
                patch.object(
                    self.runtime,
                    "native_workflow_evidence",
                    return_value=(True, []),
                ),
                self.assertRaisesRegex(RuntimeError, "fails closed"),
            ):
                self.runtime._revalidate_stop_reason(PR_NUMBER, SHA, 9, reason)


class FinalMergeGateRevalidationTest(unittest.TestCase):
    def setUp(self):
        self.runtime = load_runtime()

    def _candidate(self, *, labels=None):
        return {
            "number": PR_NUMBER,
            "state": "open",
            "draft": False,
            "mergeable": True,
            "mergeable_state": "clean",
            "head": {
                "sha": SHA,
                "ref": "fix/probe",
                "repo": {"full_name": "example/foundation"},
            },
            "base": {
                "ref": "main",
                "repo": {"full_name": "example/foundation"},
            },
            "user": {"login": "owner"},
            "labels": labels or [],
            "body": "Closes #9",
        }

    def _run_final_gate(
        self, snapshots, source_result, codex_clean_results=None
    ):
        codex_clean_results = codex_clean_results or [True, True, True]
        with (
            patch.object(self.runtime, "candidate_pulls", return_value=[snapshots[0]]),
            patch.object(self.runtime, "api", side_effect=snapshots),
            patch.object(
                self.runtime,
                "source_and_scope",
                side_effect=[
                    (9, {"number": 9}, ["docs/probe.md"], None),
                    source_result,
                ],
            ),
            patch.object(
                self.runtime,
                "attestation_attempts",
                return_value=[{"success": True, "active": False, "run_id": 1}],
            ),
            patch.object(
                self.runtime,
                "native_workflow_evidence",
                return_value=(True, []),
            ),
            patch.object(
                self.runtime,
                "exact_codex_evidence",
                return_value={
                    "state": "clean",
                    "timestamp": "2026-07-31T12:00:00Z",
                    "request_timestamp": "2026-07-31T11:00:00Z",
                },
            ),
            patch.object(
                self.runtime,
                "exact_codex_clean",
                side_effect=codex_clean_results,
            ),
            patch.object(self.runtime, "gh") as gh,
        ):
            self.runtime.supervise()
        return gh

    def test_new_ai_no_merge_label_blocks_final_merge(self):
        clean = self._candidate()
        held = self._candidate(labels=[{"name": "ai-no-merge"}])
        gh = self._run_final_gate(
            [clean, clean, clean, clean, held],
            (9, {"number": 9}, ["docs/probe.md"], None),
        )
        self.assertFalse(
            any(
                len(call.args) >= 4
                and call.args[0:3] == ("api", "--method", "PUT")
                and call.args[3].endswith("/merge")
                for call in gh.call_args_list
            )
        )

    def test_changed_issue_authorization_blocks_final_merge(self):
        clean = self._candidate()
        gh = self._run_final_gate(
            [clean, clean, clean, clean],
            (9, {"number": 9}, ["docs/probe.md"], "UNAUTHORIZED_CHANGED_PATH"),
        )
        self.assertFalse(
            any(
                len(call.args) >= 4
                and call.args[0:3] == ("api", "--method", "PUT")
                and call.args[3].endswith("/merge")
                for call in gh.call_args_list
            )
        )

    def test_clean_revalidated_scope_reaches_expected_sha_merge(self):
        clean = self._candidate()
        gh = self._run_final_gate(
            [clean, clean, clean, clean, clean, clean],
            (9, {"number": 9}, ["docs/probe.md"], None),
        )
        merge_calls = [
            call
            for call in gh.call_args_list
            if len(call.args) >= 4
            and call.args[0:3] == ("api", "--method", "PUT")
            and call.args[3].endswith("/merge")
        ]
        self.assertEqual(len(merge_calls), 1)
        self.assertIn(f"sha={SHA}", merge_calls[0].args)

    def test_late_source_issue_link_edit_blocks_merge(self):
        clean = self._candidate()
        edited = self._candidate()
        edited["body"] = "Closes #10"
        gh = self._run_final_gate(
            [clean, clean, clean, clean, edited],
            (9, {"number": 9}, ["docs/probe.md"], None),
        )
        self.assertFalse(
            any(
                len(call.args) >= 4
                and call.args[0:3] == ("api", "--method", "PUT")
                and call.args[3].endswith("/merge")
                for call in gh.call_args_list
            )
        )

    def test_late_exact_sha_codex_blocker_blocks_merge(self):
        clean = self._candidate()
        gh = self._run_final_gate(
            [clean, clean, clean],
            (9, {"number": 9}, ["docs/probe.md"], None),
            codex_clean_results=[False],
        )
        self.assertFalse(
            any(
                len(call.args) >= 4
                and call.args[0:3] == ("api", "--method", "PUT")
                and call.args[3].endswith("/merge")
                for call in gh.call_args_list
            )
        )


class FinalMergeOrderingSourceTest(unittest.TestCase):
    def test_source_scope_runs_after_last_codex_query_and_before_last_live_snapshot(self):
        runtime = open(load_runtime().__file__, encoding="utf-8").read()
        tail = runtime.split("        final = _live_pr(pr_number, sha)", 1)[1]
        tail = tail.split("        gh(\n", 1)[0]
        last_codex = tail.rfind("exact_codex_clean(pr_number, sha)")
        last_scope = tail.rfind("source_and_scope(scope_candidate)")
        last_live = tail.rfind("merge_candidate = _live_pr(pr_number, sha)")
        self.assertGreater(last_codex, -1)
        self.assertGreater(last_scope, last_codex)
        self.assertGreater(last_live, last_scope)


if __name__ == "__main__":
    unittest.main()
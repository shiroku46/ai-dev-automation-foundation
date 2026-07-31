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


if __name__ == "__main__":
    unittest.main()

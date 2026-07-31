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
        self.assertEqual(
            declared_paths(body),
            {"scripts/probe.py", "tests/**", "scripts/supervisor_runtime.py"},
        )
        self.assertTrue(
            scope_is_authorized(
                ["scripts/probe.py", "tests/unit/test_probe.py"], body
            )
        )
        self.assertFalse(
            scope_is_authorized(["scripts/probe.py", "README.md"], body)
        )

    def test_protected_paths_require_the_protected_contract(self):
        body = """
## Allowed paths
- scripts/supervisor_runtime.py
"""
        self.assertTrue(scope_is_authorized(["scripts/supervisor_runtime.py"], body))
        self.assertFalse(
            protected_scope_is_authorized(["scripts/supervisor_runtime.py"], body)
        )
        authorized = body + """
<!-- foundation-protected-authorization
paths:
- scripts/supervisor_runtime.py
operation: bounded
-->
"""
        self.assertTrue(
            protected_scope_is_authorized(
                ["scripts/supervisor_runtime.py"], authorized
            )
        )

    def test_invalid_or_unbounded_path_declarations_fail_closed(self):
        body = "## Allowed paths\n- ../outside.py\n- prose description here\n"
        self.assertEqual(declared_paths(body), set())
        self.assertFalse(scope_is_authorized(["README.md"], body))


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

    def test_complete_fixed_native_workflows_authorize_exact_sha(self):
        metadata = [
            self.metadata_result("ci.yml", 1),
            self.metadata_result("unit-tests.yml", 2),
            self.metadata_result("e2e.yml", 3),
        ]
        with (
            patch.object(self.runtime, "gh_result", side_effect=metadata),
            patch.object(
                self.runtime,
                "api_key_pages",
                side_effect=[
                    [self.make_run(1)],
                    [self.make_run(2)],
                    [self.make_run(3)],
                ],
            ),
        ):
            clean, evidence = self.runtime.native_workflow_evidence(SHA)
        self.assertTrue(clean)
        self.assertEqual(
            [item["workflow"] for item in evidence],
            ["ci.yml", "unit-tests.yml", "e2e.yml"],
        )

    def test_missing_pending_failed_and_stale_runs_fail_closed(self):
        not_found = subprocess.CompletedProcess(["gh"], 1, "", "HTTP 404 Not Found")
        cases = (
            ([], [self.make_run(2)], False),
            (
                [self.make_run(1, status="in_progress", conclusion=None)],
                [self.make_run(2)],
                False,
            ),
            ([self.make_run(1, conclusion="failure")], [self.make_run(2)], False),
            ([self.make_run(1, sha="b" * 40)], [self.make_run(2)], False),
        )
        for ci_runs, unit_runs, expected in cases:
            with self.subTest(ci_runs=ci_runs):
                with (
                    patch.object(
                        self.runtime,
                        "gh_result",
                        side_effect=[
                            self.metadata_result("ci.yml", 1),
                            self.metadata_result("unit-tests.yml", 2),
                            not_found,
                        ],
                    ),
                    patch.object(
                        self.runtime,
                        "api_key_pages",
                        side_effect=[ci_runs, unit_runs],
                    ),
                ):
                    clean, _ = self.runtime.native_workflow_evidence(SHA)
                self.assertEqual(clean, expected)

    def test_wrong_repository_or_workflow_identity_is_rejected(self):
        wrong_repo = self.make_run(1)
        wrong_repo["repository"] = {"full_name": "attacker/fork"}
        wrong_workflow = self.make_run(2)
        wrong_workflow["workflow_id"] = 99
        not_found = subprocess.CompletedProcess(["gh"], 1, "", "HTTP 404 Not Found")
        with (
            patch.object(
                self.runtime,
                "gh_result",
                side_effect=[
                    self.metadata_result("ci.yml", 1),
                    self.metadata_result("unit-tests.yml", 2),
                    not_found,
                ],
            ),
            patch.object(
                self.runtime,
                "api_key_pages",
                side_effect=[[wrong_repo], [wrong_workflow]],
            ),
        ):
            clean, evidence = self.runtime.native_workflow_evidence(SHA)
        self.assertFalse(clean)
        self.assertTrue(all(item["status"] == "missing" for item in evidence))

    def test_optional_e2e_is_not_required_when_absent(self):
        not_found = subprocess.CompletedProcess(["gh"], 1, "", "HTTP 404 Not Found")
        with (
            patch.object(
                self.runtime,
                "gh_result",
                side_effect=[
                    self.metadata_result("ci.yml", 1),
                    self.metadata_result("unit-tests.yml", 2),
                    not_found,
                ],
            ),
            patch.object(
                self.runtime,
                "api_key_pages",
                side_effect=[[self.make_run(1)], [self.make_run(2)]],
            ),
        ):
            clean, evidence = self.runtime.native_workflow_evidence(SHA)
        self.assertTrue(clean)
        self.assertEqual(len(evidence), 2)


if __name__ == "__main__":
    unittest.main()

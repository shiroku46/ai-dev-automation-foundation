"""Regression tests for deterministic post-agent workspace delta evidence."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from scripts.agent_eval_experiment_contract import parse_evaluation_experiment_plan
from scripts.agent_eval_suite_contract import load_evaluation_suite
from scripts.agent_eval_trial_delta import (
    AgentTrialDeltaError,
    inspect_agent_trial_delta,
    inspect_candidate_workspace,
    serialize_agent_trial_delta,
)
from scripts.agent_eval_trial_request import agent_trial_request_sha256, build_agent_trial_request
from scripts.agent_eval_trial_workspace import materialize_agent_trial_workspace

ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "evaluation/experiments/phase-d-initial.json"
SUITE_ROOT = ROOT / "evaluation/initial"
CATALOG_PATH = SUITE_ROOT / "catalog.json"


class AgentTrialDeltaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = parse_evaluation_experiment_plan(PLAN_PATH.read_bytes())
        cls.suite = load_evaluation_suite(CATALOG_PATH.read_bytes(), SUITE_ROOT)

    def request(self, task_id="foundation.task-001"):
        return build_agent_trial_request(self.plan, self.suite, "baseline", task_id, 1)

    def workspace(self, root: Path, request):
        destination = root / "workspace"
        materialize_agent_trial_workspace(request, self.suite, SUITE_ROOT, destination)
        return destination

    def test_allowed_single_file_edit_and_deterministic_serialization(self):
        request = self.request()
        with tempfile.TemporaryDirectory() as temp:
            workspace = self.workspace(Path(temp), request)
            path = workspace / "src/ranges.py"
            path.write_text(path.read_text(encoding="utf-8").replace("min(lower", "min(upper"), encoding="utf-8")
            first = inspect_agent_trial_delta(request, workspace)
            second = inspect_agent_trial_delta(request, workspace)
            self.assertEqual(first, second)
            self.assertEqual(serialize_agent_trial_delta(first), serialize_agent_trial_delta(second))
            self.assertEqual(first.request_sha256, agent_trial_request_sha256(request))
            self.assertEqual(first.modified_content_paths, ("src/ranges.py",))
            self.assertEqual(first.changed_paths, ("src/ranges.py",))
            self.assertEqual(first.scope_violation_paths, ())
            self.assertEqual(first.mutation_count, 1)
            raw = serialize_agent_trial_delta(first)
            self.assertEqual(raw, json.dumps(json.loads(raw), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))

    def test_out_of_scope_addition_is_never_hidden(self):
        request = self.request()
        with tempfile.TemporaryDirectory() as temp:
            workspace = self.workspace(Path(temp), request)
            (workspace / "notes.txt").write_text("outside scope\n", encoding="utf-8")
            delta = inspect_agent_trial_delta(request, workspace)
            self.assertEqual(delta.added_paths, ("notes.txt",))
            self.assertEqual(delta.scope_violation_paths, ("notes.txt",))
            self.assertEqual(delta.scope_violation_count, 1)

    def test_exact_rename_records_delete_and_add_without_scope_violation(self):
        request = self.request("foundation.task-017")
        with tempfile.TemporaryDirectory() as temp:
            workspace = self.workspace(Path(temp), request)
            (workspace / "src/old_name.py").rename(workspace / "src/new_name.py")
            delta = inspect_agent_trial_delta(request, workspace)
            self.assertEqual(delta.added_paths, ("src/new_name.py",))
            self.assertEqual(delta.deleted_paths, ("src/old_name.py",))
            self.assertEqual(delta.changed_paths, ("src/new_name.py", "src/old_name.py"))
            self.assertEqual(delta.scope_violation_paths, ())

    def test_unchanged_and_empty_candidate_workspaces_are_measurable(self):
        request = self.request("foundation.task-006")
        with tempfile.TemporaryDirectory() as temp:
            workspace = self.workspace(Path(temp), request)
            delta = inspect_agent_trial_delta(request, workspace)
            self.assertEqual(delta.changed_paths, ())
            self.assertEqual(delta.mutation_count, 0)

        request = self.request("foundation.task-008")
        self.assertEqual(request.fixture_bundle.file_count, 1)
        with tempfile.TemporaryDirectory() as temp:
            workspace = self.workspace(Path(temp), request)
            only = request.fixture_bundle.files[0].path
            (workspace / only).unlink()
            delta = inspect_agent_trial_delta(request, workspace)
            self.assertEqual(delta.candidate_file_count, 0)
            self.assertEqual(delta.deleted_paths, (only,))
            self.assertEqual(delta.mutation_count, 1)

    def test_executable_bit_only_change_is_detected(self):
        request = self.request()
        with tempfile.TemporaryDirectory() as temp:
            workspace = self.workspace(Path(temp), request)
            path = workspace / "src/ranges.py"
            path.chmod(path.stat().st_mode | 0o111)
            delta = inspect_agent_trial_delta(request, workspace)
            self.assertEqual(delta.modified_content_paths, ())
            self.assertEqual(delta.executable_changed_paths, ("src/ranges.py",))
            self.assertEqual(delta.scope_violation_paths, ())

    def test_bounded_scope_pattern_is_not_broadened(self):
        request = replace(self.request(), allowed_paths=("src/**",))
        with tempfile.TemporaryDirectory() as temp:
            workspace = self.workspace(Path(temp), self.request())
            source = workspace / "src/ranges.py"
            source.write_text(source.read_text(encoding="utf-8") + "\n# in scope\n", encoding="utf-8")
            (workspace / "tests/extra.py").write_text("outside\n", encoding="utf-8")
            delta = inspect_agent_trial_delta(request, workspace)
            self.assertIn("src/ranges.py", delta.changed_paths)
            self.assertEqual(delta.scope_violation_paths, ("tests/extra.py",))

    @unittest.skipIf(os.name == "nt", "link setup is not portable on Windows")
    def test_symlink_and_hard_link_candidates_fail_closed(self):
        request = self.request()
        with tempfile.TemporaryDirectory() as temp:
            workspace = self.workspace(Path(temp), request)
            (workspace / "link.py").symlink_to(workspace / "src/ranges.py")
            with self.assertRaises(AgentTrialDeltaError):
                inspect_agent_trial_delta(request, workspace)
        with tempfile.TemporaryDirectory() as temp:
            workspace = self.workspace(Path(temp), request)
            os.link(workspace / "src/ranges.py", workspace / "hard.py")
            with self.assertRaises(AgentTrialDeltaError):
                inspect_agent_trial_delta(request, workspace)

    def test_case_collision_oversize_and_tampered_request_fail_closed(self):
        request = self.request()
        with tempfile.TemporaryDirectory() as temp:
            workspace = self.workspace(Path(temp), request)
            collision = workspace / "SRC"
            collision.mkdir()
            (collision / "other.py").write_text("collision\n", encoding="utf-8")
            with self.assertRaises(AgentTrialDeltaError):
                inspect_agent_trial_delta(request, workspace)

        with tempfile.TemporaryDirectory() as temp:
            workspace = self.workspace(Path(temp), request)
            with patch("scripts.agent_eval_trial_delta.MAX_FILE_BYTES", 1):
                with self.assertRaises(AgentTrialDeltaError):
                    inspect_candidate_workspace(workspace)

        bad_bundle = replace(request.fixture_bundle, file_count=request.fixture_bundle.file_count + 1)
        tampered = replace(request, fixture_bundle=bad_bundle)
        with tempfile.TemporaryDirectory() as temp:
            workspace = self.workspace(Path(temp), request)
            with self.assertRaises(AgentTrialDeltaError):
                inspect_agent_trial_delta(tampered, workspace)


if __name__ == "__main__":
    unittest.main()

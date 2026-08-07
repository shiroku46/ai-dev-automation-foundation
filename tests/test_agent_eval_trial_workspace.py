"""Regression tests for sealed fixture-only trial workspace materialization."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from scripts.agent_eval_experiment_contract import parse_evaluation_experiment_plan
from scripts.agent_eval_suite_contract import inspect_directory_bundle, load_evaluation_suite
from scripts.agent_eval_trial_request import AgentFixtureBundle, build_agent_trial_request
from scripts.agent_eval_trial_workspace import (
    AgentTrialWorkspaceError,
    agent_trial_request_sha256,
    materialize_agent_trial_workspace,
)

ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "evaluation/experiments/phase-d-initial.json"
SUITE_ROOT = ROOT / "evaluation/initial"
CATALOG_PATH = SUITE_ROOT / "catalog.json"


class AgentTrialWorkspaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = parse_evaluation_experiment_plan(PLAN_PATH.read_bytes())
        cls.suite = load_evaluation_suite(CATALOG_PATH.read_bytes(), SUITE_ROOT)

    def request(self, task_id="foundation.task-001"):
        return build_agent_trial_request(self.plan, self.suite, "baseline", task_id, 1)

    def test_materializes_exact_fixture_index_without_foundation_or_grader_leakage(self):
        for task_id in (
            "foundation.task-001",
            "foundation.task-005",
            "foundation.task-017",
            "foundation.task-030",
        ):
            request = self.request(task_id)
            with self.subTest(task=task_id), tempfile.TemporaryDirectory() as temp:
                destination = Path(temp) / "workspace"
                evidence = materialize_agent_trial_workspace(request, self.suite, SUITE_ROOT, destination)
                bundle = inspect_directory_bundle(destination)
                expected_paths = tuple(item.path for item in request.fixture_bundle.files)
                observed_paths = tuple(item.path for item in bundle.files)
                self.assertEqual(observed_paths, expected_paths)
                self.assertEqual(bundle.sha256, request.fixture_bundle.sha256)
                self.assertEqual(evidence.fixture_sha256, request.fixture_bundle.sha256)
                self.assertEqual(evidence.request_sha256, agent_trial_request_sha256(request))
                self.assertEqual(evidence.file_count, request.fixture_bundle.file_count)
                self.assertEqual(evidence.uncompressed_bytes, request.fixture_bundle.uncompressed_bytes)
                self.assertEqual(Path(evidence.destination), destination)
                relative = {path.relative_to(destination).as_posix() for path in destination.rglob("*") if path.is_file()}
                self.assertEqual(relative, set(expected_paths))
                self.assertFalse(any(path.startswith("graders/") for path in relative))
                self.assertFalse(any(path.startswith("scripts/") for path in relative))
                self.assertNotIn("tests/test_agent_eval_initial_suite.py", relative)
                self.assertFalse((destination / ".git").exists())

    def test_existing_destination_and_tampered_request_fail_before_materialization(self):
        request = self.request()
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "workspace"
            destination.mkdir()
            with self.assertRaises(AgentTrialWorkspaceError):
                materialize_agent_trial_workspace(request, self.suite, SUITE_ROOT, destination)

        tampered = replace(request, foundation_sha="f" * 40)
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(AgentTrialWorkspaceError):
            materialize_agent_trial_workspace(tampered, self.suite, SUITE_ROOT, Path(temp) / "workspace")

        files = list(request.fixture_bundle.files)
        files[0] = replace(files[0], size=files[0].size + 1)
        bundle = replace(request.fixture_bundle, files=tuple(files))
        tampered = replace(request, fixture_bundle=bundle)
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(AgentTrialWorkspaceError):
            materialize_agent_trial_workspace(tampered, self.suite, SUITE_ROOT, Path(temp) / "workspace")

    def copied_suite(self, temp: str) -> Path:
        copied = Path(temp) / "initial"
        shutil.copytree(SUITE_ROOT, copied)
        return copied

    def test_changed_source_and_unexpected_source_file_fail_closed(self):
        request = self.request()
        with tempfile.TemporaryDirectory() as temp:
            copied = self.copied_suite(temp)
            source = copied / "fixtures/foundation.task-001/src/ranges.py"
            source.write_text(source.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
            with self.assertRaises(Exception):
                materialize_agent_trial_workspace(request, self.suite, copied, Path(temp) / "workspace")
            self.assertFalse((Path(temp) / "workspace").exists())

        with tempfile.TemporaryDirectory() as temp:
            copied = self.copied_suite(temp)
            extra = copied / "fixtures/foundation.task-001/extra.txt"
            extra.write_text("unexpected\n", encoding="utf-8")
            with self.assertRaises(Exception):
                materialize_agent_trial_workspace(request, self.suite, copied, Path(temp) / "workspace")
            self.assertFalse((Path(temp) / "workspace").exists())

    @unittest.skipIf(os.name == "nt", "symlink setup is not portable on Windows")
    def test_symlinked_source_file_fails_closed(self):
        request = self.request()
        with tempfile.TemporaryDirectory() as temp:
            copied = self.copied_suite(temp)
            source = copied / "fixtures/foundation.task-001/src/ranges.py"
            external = Path(temp) / "external.py"
            external.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            source.unlink()
            source.symlink_to(external)
            with self.assertRaises(Exception):
                materialize_agent_trial_workspace(request, self.suite, copied, Path(temp) / "workspace")
            self.assertFalse((Path(temp) / "workspace").exists())

    @unittest.skipIf(os.name == "nt", "hard-link setup is not portable on Windows")
    def test_hard_linked_source_file_fails_closed(self):
        request = self.request()
        with tempfile.TemporaryDirectory() as temp:
            copied = self.copied_suite(temp)
            source = copied / "fixtures/foundation.task-001/src/ranges.py"
            os.link(source, Path(temp) / "second-link.py")
            with self.assertRaises(Exception):
                materialize_agent_trial_workspace(request, self.suite, copied, Path(temp) / "workspace")
            self.assertFalse((Path(temp) / "workspace").exists())

    def test_bounded_source_read_rejects_file_above_configured_limit(self):
        request = self.request()
        with tempfile.TemporaryDirectory() as temp, patch(
            "scripts.agent_eval_trial_workspace.MAX_SOURCE_FILE_BYTES", 1
        ):
            with self.assertRaises(AgentTrialWorkspaceError):
                materialize_agent_trial_workspace(request, self.suite, SUITE_ROOT, Path(temp) / "workspace")
            self.assertFalse((Path(temp) / "workspace").exists())


if __name__ == "__main__":
    unittest.main()

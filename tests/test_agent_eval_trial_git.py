"""Regression tests for deterministic local Git identity over sealed trial workspaces."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from scripts.agent_eval_experiment_contract import parse_evaluation_experiment_plan
from scripts.agent_eval_suite_contract import load_evaluation_suite
from scripts.agent_eval_trial_delta import inspect_agent_trial_delta
from scripts.agent_eval_trial_git import (
    AgentTrialGitError,
    finalize_trial_git_identity,
    initialize_trial_git_identity,
)
from scripts.agent_eval_trial_request import build_agent_trial_request
from scripts.agent_eval_trial_workspace import materialize_agent_trial_workspace

ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "evaluation/experiments/phase-d-initial.json"
SUITE_ROOT = ROOT / "evaluation/initial"
CATALOG_PATH = SUITE_ROOT / "catalog.json"


class AgentTrialGitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("git") is None:
            raise unittest.SkipTest("git is unavailable")
        cls.plan = parse_evaluation_experiment_plan(PLAN_PATH.read_bytes())
        cls.suite = load_evaluation_suite(CATALOG_PATH.read_bytes(), SUITE_ROOT)

    def request(self, task_id="foundation.task-001", trial=1):
        return build_agent_trial_request(self.plan, self.suite, "baseline", task_id, trial)

    def prepare(self, root: Path, task_id="foundation.task-001", trial=1):
        request = self.request(task_id, trial)
        workspace = root / "workspace"
        materialize_agent_trial_workspace(request, self.suite, SUITE_ROOT, workspace)
        initialized = initialize_trial_git_identity(request, workspace, root / "git-metadata")
        return request, workspace, initialized

    def git(self, metadata: str, *args: str) -> str:
        executable = shutil.which("git")
        completed = subprocess.run(
            [executable, "--git-dir", metadata, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
            env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
        )
        return completed.stdout.strip()

    def test_identical_fixture_inputs_produce_identical_baseline_sha(self):
        results = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as temp:
                request, workspace, initialized = self.prepare(Path(temp))
                results.append((initialized.base_sha, initialized.baseline_tree_sha))
                self.assertEqual(initialized.baseline_bundle_sha256, request.fixture_bundle.sha256)
                self.assertFalse((workspace / ".git").exists())
                self.assertFalse(Path(initialized.metadata_dir).is_relative_to(workspace))
                self.assertEqual(self.git(initialized.metadata_dir, "remote"), "")
                self.assertEqual(self.git(initialized.metadata_dir, "rev-parse", "--show-object-format"), "sha1")
        self.assertEqual(results[0], results[1])

    def test_identical_and_changed_candidate_bytes_have_deterministic_sha_behavior(self):
        unchanged = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as temp:
                request, _, initialized = self.prepare(Path(temp))
                finalized = finalize_trial_git_identity(request, initialized)
                unchanged.append(finalized.candidate_sha)
                self.assertNotEqual(finalized.base_sha, finalized.candidate_sha)
                self.assertEqual(finalized.mutation_count, 0)
        self.assertEqual(unchanged[0], unchanged[1])

        changed = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as temp:
                request, workspace, initialized = self.prepare(Path(temp))
                path = workspace / "src/ranges.py"
                path.write_text(path.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
                finalized = finalize_trial_git_identity(request, initialized)
                changed.append(finalized.candidate_sha)
                self.assertEqual(finalized.mutation_count, 1)
        self.assertEqual(changed[0], changed[1])
        self.assertNotEqual(unchanged[0], changed[0])

    def test_candidate_parent_tree_and_workspace_paths_are_exact(self):
        with tempfile.TemporaryDirectory() as temp:
            request, workspace, initialized = self.prepare(Path(temp))
            path = workspace / "src/ranges.py"
            path.write_text(path.read_text(encoding="utf-8") + "\n# candidate\n", encoding="utf-8")
            finalized = finalize_trial_git_identity(request, initialized)
            self.assertEqual(self.git(initialized.metadata_dir, "rev-parse", f"{finalized.candidate_sha}^"), initialized.base_sha)
            self.assertEqual(self.git(initialized.metadata_dir, "rev-parse", f"{finalized.candidate_sha}^{{tree}}"), finalized.candidate_tree_sha)
            git_paths = tuple(sorted(filter(None, self.git(initialized.metadata_dir, "ls-tree", "-r", "--name-only", finalized.candidate_sha).splitlines())))
            workspace_paths = tuple(sorted(path.relative_to(workspace).as_posix() for path in workspace.rglob("*") if path.is_file()))
            self.assertEqual(git_paths, workspace_paths)
            self.assertFalse((workspace / ".git").exists())

    def test_exact_rename_deletion_and_no_change_cases_create_valid_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            request, workspace, initialized = self.prepare(Path(temp), "foundation.task-017")
            (workspace / "src/old_name.py").rename(workspace / "src/new_name.py")
            finalized = finalize_trial_git_identity(request, initialized)
            self.assertEqual(finalized.mutation_count, 2)
            self.assertEqual(finalized.scope_violation_count, 0)

        with tempfile.TemporaryDirectory() as temp:
            request, workspace, initialized = self.prepare(Path(temp), "foundation.task-008")
            for item in request.fixture_bundle.files:
                (workspace / item.path).unlink()
            finalized = finalize_trial_git_identity(request, initialized)
            self.assertEqual(finalized.mutation_count, 1)
            self.assertEqual(self.git(initialized.metadata_dir, "ls-tree", "-r", "--name-only", finalized.candidate_sha), "")

        with tempfile.TemporaryDirectory() as temp:
            request, _, initialized = self.prepare(Path(temp), "foundation.task-006")
            finalized = finalize_trial_git_identity(request, initialized)
            self.assertEqual(finalized.mutation_count, 0)
            self.assertNotEqual(finalized.base_sha, finalized.candidate_sha)

    def test_initialization_rejects_mutated_existing_nested_and_missing_git(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request = self.request()
            workspace = root / "workspace"
            materialize_agent_trial_workspace(request, self.suite, SUITE_ROOT, workspace)
            path = workspace / "src/ranges.py"
            path.write_text(path.read_text(encoding="utf-8") + "\n# early mutation\n", encoding="utf-8")
            with self.assertRaises(AgentTrialGitError):
                initialize_trial_git_identity(request, workspace, root / "git-metadata")

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request = self.request()
            workspace = root / "workspace"
            materialize_agent_trial_workspace(request, self.suite, SUITE_ROOT, workspace)
            metadata = root / "git-metadata"
            metadata.mkdir()
            with self.assertRaises(AgentTrialGitError):
                initialize_trial_git_identity(request, workspace, metadata)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request = self.request()
            workspace = root / "workspace"
            materialize_agent_trial_workspace(request, self.suite, SUITE_ROOT, workspace)
            with self.assertRaises(AgentTrialGitError):
                initialize_trial_git_identity(request, workspace, workspace / "metadata")

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request = self.request()
            workspace = root / "workspace"
            materialize_agent_trial_workspace(request, self.suite, SUITE_ROOT, workspace)
            with patch("scripts.agent_eval_trial_git.shutil.which", return_value=None):
                with self.assertRaises(AgentTrialGitError):
                    initialize_trial_git_identity(request, workspace, root / "git-metadata")

    def test_finalization_rejects_moved_baseline_unsafe_workspace_and_changed_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request, _, initialized = self.prepare(root)
            subprocess.run(
                [shutil.which("git"), "--git-dir", initialized.metadata_dir, "update-ref", "-d", "refs/heads/baseline"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            with self.assertRaises(AgentTrialGitError):
                finalize_trial_git_identity(request, initialized)

        if os.name != "nt":
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                request, workspace, initialized = self.prepare(root)
                backup = root / "backup"
                workspace.rename(backup)
                workspace.symlink_to(backup, target_is_directory=True)
                with self.assertRaises(Exception):
                    finalize_trial_git_identity(request, initialized)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request, workspace, initialized = self.prepare(root)
            path = workspace / "src/ranges.py"
            path.write_text(path.read_text(encoding="utf-8") + "\n# candidate\n", encoding="utf-8")
            before = inspect_agent_trial_delta(request, workspace)
            after = replace(before, candidate_bundle_sha256="f" * 64)
            with patch("scripts.agent_eval_trial_git.inspect_agent_trial_delta", side_effect=[before, after]):
                with self.assertRaises(AgentTrialGitError):
                    finalize_trial_git_identity(request, initialized)

    def test_altered_initialization_evidence_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            request, _, initialized = self.prepare(Path(temp))
            with self.assertRaises(AgentTrialGitError):
                finalize_trial_git_identity(request, replace(initialized, request_sha256="0" * 64))
            with self.assertRaises(AgentTrialGitError):
                finalize_trial_git_identity(request, replace(initialized, baseline_tree_sha="0" * 40))


if __name__ == "__main__":
    unittest.main()

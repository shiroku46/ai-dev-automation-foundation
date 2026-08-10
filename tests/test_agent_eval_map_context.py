"""Regression tests for optional map-assisted Phase D agent-visible requests."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from scripts.agent_eval_experiment_contract import parse_evaluation_experiment_plan
from scripts.agent_eval_map_context import (
    MapAssistedTrialRequestError,
    build_map_assisted_trial_request,
    serialize_map_assisted_trial_request,
)
from scripts.agent_eval_suite_contract import load_evaluation_suite
from scripts.agent_eval_trial_request import serialize_agent_trial_request
from scripts.agent_eval_trial_session import prepare_evaluation_session

ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "evaluation/experiments/phase-d-initial.json"
SUITE_ROOT = ROOT / "evaluation/initial"
CATALOG_PATH = SUITE_ROOT / "catalog.json"


class MapAssistedTrialRequestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("git") is None:
            raise unittest.SkipTest("git is unavailable")
        cls.plan = parse_evaluation_experiment_plan(PLAN_PATH.read_bytes())
        cls.suite = load_evaluation_suite(CATALOG_PATH.read_bytes(), SUITE_ROOT)

    def prepare(self, root: Path, task_id: str = "foundation.task-001"):
        return prepare_evaluation_session(
            self.plan,
            self.suite,
            SUITE_ROOT,
            "baseline",
            task_id,
            1,
            root / "workspace",
            root / "git-metadata",
        )

    def build(self, session, seed: str, *, max_depth: int = 2):
        return build_map_assisted_trial_request(
            self.plan,
            self.suite,
            session,
            (seed,),
            max_depth=max_depth,
            max_paths=64,
        )

    def git_dir(self, metadata: Path, *args: str) -> str:
        completed = subprocess.run(
            ["git", f"--git-dir={metadata}", *args],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        return completed.stdout.strip()

    def test_ordinary_protected_and_handoff_sessions_build_before_mutation(self):
        cases = (
            ("foundation.task-001", "src/ranges.py"),
            ("foundation.task-005", ".github/workflows/check.yml"),
            ("foundation.task-030", "src/resume.py"),
        )
        for task_id, seed in cases:
            with self.subTest(task=task_id), tempfile.TemporaryDirectory() as temp:
                session = self.prepare(Path(temp), task_id)
                evidence = self.build(session, seed)
                self.assertEqual(evidence.request, session.trial.request)
                self.assertEqual(evidence.request_bytes, serialize_agent_trial_request(session.trial.request))
                self.assertEqual(evidence.repository_sha, session.git.base_sha)
                self.assertEqual(evidence.repository_tree_sha, session.git.baseline_tree_sha)
                self.assertEqual(evidence.context.trusted_allowed_paths, session.trial.request.allowed_paths)
                self.assertEqual(serialize_map_assisted_trial_request(evidence), evidence.wrapper_bytes)
                self.assertFalse((Path(session.trial.destination) / ".git").exists())

    def test_wrapper_contains_only_original_request_context_and_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            session = self.prepare(Path(temp))
            evidence = self.build(session, "src/ranges.py")
            wrapper = json.loads(evidence.wrapper_bytes)
            self.assertEqual(
                set(wrapper),
                {"schema_version", "mode", "trial_request", "repository_context", "wrapper_sha256"},
            )
            self.assertEqual(wrapper["schema_version"], 1)
            self.assertEqual(wrapper["mode"], "map-assisted")
            self.assertEqual(wrapper["trial_request"], json.loads(evidence.request_bytes))
            self.assertEqual(wrapper["repository_context"], json.loads(evidence.context_bytes))
            self.assertEqual(wrapper["wrapper_sha256"], evidence.wrapper_sha256)

            forbidden_keys = {
                "workspace",
                "workspace_path",
                "metadata",
                "metadata_dir",
                "git_metadata",
                "grader",
                "grader_root",
                "ground_truth",
                "known_solution",
                "transcript",
                "hidden_reasoning",
                "expected_completion_class",
                "credential",
                "credentials",
            }

            def visit(value):
                if isinstance(value, dict):
                    for key, child in value.items():
                        self.assertNotIn(key.casefold(), forbidden_keys)
                        visit(child)
                elif isinstance(value, list):
                    for child in value:
                        visit(child)

            visit(wrapper)

    def test_map_context_can_add_impacted_test_without_widening_write_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            session = self.prepare(Path(temp))
            evidence = self.build(session, "src/ranges.py")
            self.assertEqual(evidence.request.allowed_paths, ("src/ranges.py",))
            self.assertEqual(evidence.context.trusted_allowed_paths, ("src/ranges.py",))
            self.assertIn("tests/test_ranges.py", evidence.context.dependent_paths)
            self.assertIn("tests/test_ranges.py", evidence.context.test_paths)
            self.assertIn("tests/test_ranges.py", evidence.context.read_paths)
            context_object = json.loads(evidence.context_bytes)
            self.assertNotIn("expanded_allowed_paths", context_object)
            self.assertNotIn("writable_paths", context_object)

    def test_identical_inputs_are_deterministic_and_depth_changes_only_wrapper_context(self):
        with tempfile.TemporaryDirectory() as temp:
            session = self.prepare(Path(temp))
            first = self.build(session, "src/ranges.py", max_depth=2)
            second = self.build(session, "src/ranges.py", max_depth=2)
            shallow = self.build(session, "src/ranges.py", max_depth=0)
            self.assertEqual(first, second)
            self.assertEqual(first.wrapper_bytes, second.wrapper_bytes)
            self.assertEqual(first.wrapper_sha256, second.wrapper_sha256)
            self.assertNotEqual(first.wrapper_sha256, shallow.wrapper_sha256)
            self.assertNotEqual(first.context.package_sha256, shallow.context.package_sha256)
            self.assertEqual(first.request_bytes, shallow.request_bytes)
            self.assertEqual(first.request_sha256, shallow.request_sha256)
            self.assertEqual(first.request.allowed_paths, shallow.request.allowed_paths)

    def test_unknown_unsorted_and_duplicate_seed_paths_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            session = self.prepare(Path(temp))
            for seeds in (
                ("missing.py",),
                ("tests/test_ranges.py", "src/ranges.py"),
                ("src/ranges.py", "src/ranges.py"),
            ):
                with self.subTest(seeds=seeds), self.assertRaises(MapAssistedTrialRequestError):
                    build_map_assisted_trial_request(
                        self.plan,
                        self.suite,
                        session,
                        seeds,
                    )

    def test_workspace_mutation_tampered_session_moved_ref_and_remote_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            session = self.prepare(Path(temp))
            workspace = Path(session.trial.destination)
            path = workspace / "src/ranges.py"
            path.write_text(path.read_text(encoding="utf-8") + "\n# mutation\n", encoding="utf-8")
            with self.assertRaises(MapAssistedTrialRequestError):
                self.build(session, "src/ranges.py")

        with tempfile.TemporaryDirectory() as temp:
            session = self.prepare(Path(temp))
            tampered = replace(
                session,
                git=replace(session.git, baseline_tree_sha="0" * 40),
            )
            with self.assertRaises(MapAssistedTrialRequestError):
                self.build(tampered, "src/ranges.py")

        with tempfile.TemporaryDirectory() as temp:
            session = self.prepare(Path(temp))
            metadata = Path(session.git.metadata_dir)
            tree = self.git_dir(metadata, "rev-parse", f"{session.git.base_sha}^{{tree}}")
            other = self.git_dir(metadata, "commit-tree", tree, "-m", "moved baseline")
            self.git_dir(metadata, "update-ref", "refs/heads/baseline", other)
            with self.assertRaises(MapAssistedTrialRequestError):
                self.build(session, "src/ranges.py")

        with tempfile.TemporaryDirectory() as temp:
            session = self.prepare(Path(temp))
            metadata = Path(session.git.metadata_dir)
            self.git_dir(metadata, "remote", "add", "blocked", "https://example.invalid/repository")
            with self.assertRaises(MapAssistedTrialRequestError):
                self.build(session, "src/ranges.py")

    def test_tampered_return_evidence_fails_serialization(self):
        with tempfile.TemporaryDirectory() as temp:
            session = self.prepare(Path(temp))
            evidence = self.build(session, "src/ranges.py")
            with self.assertRaises(MapAssistedTrialRequestError):
                serialize_map_assisted_trial_request(
                    replace(evidence, wrapper_sha256="0" * 64)
                )
            with self.assertRaises(MapAssistedTrialRequestError):
                serialize_map_assisted_trial_request(
                    replace(evidence, repository_map_sha256="0" * 64)
                )


if __name__ == "__main__":
    unittest.main()

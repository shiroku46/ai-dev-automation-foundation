"""Regression tests for Git-bound Phase D trial sessions and grader invocation specs."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.agent_eval_contract import EnvironmentFacts, parse_evaluation_run
from scripts.agent_eval_experiment_contract import parse_evaluation_experiment_plan
from scripts.agent_eval_grader_contract import GRADER_IDENTITY_ENVIRONMENT_KEYS
from scripts.agent_eval_run_assembly import TrialRuntimeObservation
from scripts.agent_eval_suite_contract import load_evaluation_suite
from scripts.agent_eval_trial_session import (
    EvaluationTrialSessionError,
    build_session_grader_invocation,
    finalize_graded_evaluation_session,
    finalize_infrastructure_evaluation_session,
    freeze_evaluation_session,
    prepare_evaluation_session,
)

ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "evaluation/experiments/phase-d-initial.json"
SUITE_ROOT = ROOT / "evaluation/initial"
CATALOG_PATH = SUITE_ROOT / "catalog.json"


class EvaluationTrialSessionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("git") is None:
            raise unittest.SkipTest("git is unavailable")
        cls.plan = parse_evaluation_experiment_plan(PLAN_PATH.read_bytes())
        cls.suite = load_evaluation_suite(CATALOG_PATH.read_bytes(), SUITE_ROOT)
        cls.environment = EnvironmentFacts(
            os="ubuntu-24.04",
            architecture="x86_64",
            python="3.12.0",
            cpu_count=2,
            memory_mib=4096,
            timeout_seconds=900,
            network_mode="disabled",
            tool_versions=(("git", "2.50.0"),),
        )

    def observation(self, *, handoff="not_applicable"):
        return TrialRuntimeObservation(
            environment=self.environment,
            started_at=datetime(2026, 8, 7, 0, 0, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 8, 7, 0, 1, 0, tzinfo=timezone.utc),
            iterations=1,
            github_api_requests=12,
            actions_minutes=1.5,
            estimated_cost_usd=None,
            human_action_requests=0,
            confirmed_human_actions=0,
            false_human_action_requests=0,
            handoff_recovery=handoff,
            unresolved_review_threads=0,
        )

    def prepare(self, root: Path, task_id="foundation.task-001"):
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

    def solve_task_one(self, workspace: Path):
        path = workspace / "src/ranges.py"
        source = path.read_text(encoding="utf-8")
        path.write_text(
            source.replace("return min(lower, max(value, upper))", "return min(upper, max(value, lower))"),
            encoding="utf-8",
        )

    def run_spec(self, spec):
        executable = shutil.which(spec.argv[0])
        if executable is None:
            self.skipTest(f"grader runtime is unavailable: {spec.argv[0]}")
        completed = subprocess.run(
            [executable, *spec.argv[1:]],
            cwd=spec.cwd,
            env={**dict(spec.identity_environment), "PYTHONDONTWRITEBYTECODE": "1"},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=spec.timeout_seconds,
            check=False,
        )
        result_path = Path(spec.argv[-1])
        self.assertTrue(result_path.is_file())
        return completed.returncode, result_path.read_bytes()

    def test_prepare_ordinary_protected_and_handoff_sessions_bind_real_baseline(self):
        for task_id in ("foundation.task-001", "foundation.task-005", "foundation.task-030"):
            with self.subTest(task=task_id), tempfile.TemporaryDirectory() as temp:
                session = self.prepare(Path(temp), task_id)
                self.assertEqual(len(session.git.base_sha), 40)
                self.assertEqual(session.git.request_sha256, session.trial.request_sha256)
                self.assertEqual(session.git.baseline_bundle_sha256, session.trial.request.fixture_bundle.sha256)
                self.assertEqual(Path(session.git.workspace), Path(session.trial.destination))
                self.assertFalse((Path(session.trial.destination) / ".git").exists())

    def test_freeze_binds_candidate_sha_bundle_and_scope_counts(self):
        with tempfile.TemporaryDirectory() as temp:
            session = self.prepare(Path(temp))
            self.solve_task_one(Path(session.trial.destination))
            frozen = freeze_evaluation_session(self.plan, self.suite, session)
            self.assertNotEqual(frozen.base_sha, frozen.candidate_sha)
            self.assertEqual(len(frozen.candidate_sha), 40)
            self.assertEqual(frozen.git.mutation_count, 1)
            self.assertEqual(frozen.git.scope_violation_count, 0)

        with tempfile.TemporaryDirectory() as temp:
            session = self.prepare(Path(temp))
            (Path(session.trial.destination) / "notes.txt").write_text("outside\n", encoding="utf-8")
            frozen = freeze_evaluation_session(self.plan, self.suite, session)
            self.assertEqual(frozen.git.scope_violation_count, 1)

    def test_grader_spec_is_exact_minimal_and_credential_free(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            session = self.prepare(root)
            self.solve_task_one(Path(session.trial.destination))
            frozen = freeze_evaluation_session(self.plan, self.suite, session)
            spec = build_session_grader_invocation(
                self.plan,
                self.suite,
                SUITE_ROOT,
                frozen,
                root / "grader-result.json",
            )
            task = next(item for item in self.suite.tasks if item.entry.task_id == frozen.prepared.trial.task_id)
            expected_cwd = (SUITE_ROOT / task.entry.grader_root).resolve()
            self.assertEqual(Path(spec.cwd), expected_cwd)
            self.assertEqual(spec.argv[2:4], ("--workspace", str(Path(session.trial.destination).resolve())))
            self.assertEqual(spec.argv[4], "--result")
            self.assertEqual(spec.expected.base_sha, frozen.base_sha)
            self.assertEqual(spec.expected.candidate_sha, frozen.candidate_sha)
            self.assertEqual(tuple(key for key, _ in spec.identity_environment), GRADER_IDENTITY_ENVIRONMENT_KEYS)
            self.assertEqual(len(spec.identity_environment), 7)
            self.assertEqual(spec.timeout_seconds, task.manifest.grader.timeout_seconds)
            self.assertEqual(spec.network_mode, "disabled")
            forbidden = ("TOKEN", "SECRET", "PASSWORD", "KEY", "GITHUB")
            self.assertFalse(any(any(word in key.upper() for word in forbidden) for key, _ in spec.identity_environment))

    def test_external_trusted_grader_spec_finalizes_passed_run_with_frozen_candidate_sha(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            session = self.prepare(root)
            self.solve_task_one(Path(session.trial.destination))
            frozen = freeze_evaluation_session(self.plan, self.suite, session)
            spec = build_session_grader_invocation(self.plan, self.suite, SUITE_ROOT, frozen, root / "result.json")
            exit_code, content = self.run_spec(spec)
            self.assertEqual(exit_code, 0)
            finalized = finalize_graded_evaluation_session(
                self.plan,
                self.suite,
                frozen,
                grader_result_content=content,
                grader_exit_code=exit_code,
                observation=self.observation(),
            )
            run = parse_evaluation_run(finalized.run_record)
            self.assertEqual(run.outcome, "passed")
            self.assertEqual(run.candidate_sha, frozen.candidate_sha)
            self.assertEqual(run.metrics.scope_violation_attempts, 0)

    def test_out_of_scope_mutation_survives_external_grader_as_safety_scope_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            session = self.prepare(root)
            workspace = Path(session.trial.destination)
            self.solve_task_one(workspace)
            (workspace / "notes.txt").write_text("outside\n", encoding="utf-8")
            frozen = freeze_evaluation_session(self.plan, self.suite, session)
            spec = build_session_grader_invocation(self.plan, self.suite, SUITE_ROOT, frozen, root / "result.json")
            exit_code, content = self.run_spec(spec)
            self.assertEqual(exit_code, 0)
            finalized = finalize_graded_evaluation_session(
                self.plan,
                self.suite,
                frozen,
                grader_result_content=content,
                grader_exit_code=exit_code,
                observation=self.observation(),
            )
            run = parse_evaluation_run(finalized.run_record)
            self.assertEqual(run.outcome, "failed")
            self.assertEqual(run.failure_class, "safety_scope")
            self.assertEqual(run.metrics.scope_violation_attempts, 1)

    def test_post_freeze_mutation_fails_before_grader_evidence_is_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            session = self.prepare(root)
            frozen = freeze_evaluation_session(self.plan, self.suite, session)
            path = Path(session.trial.destination) / "src/ranges.py"
            path.write_text(path.read_text(encoding="utf-8") + "\n# after freeze\n", encoding="utf-8")
            with self.assertRaises(Exception):
                build_session_grader_invocation(self.plan, self.suite, SUITE_ROOT, frozen, root / "result.json")

    def test_tampered_grader_root_and_overlapping_result_paths_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            session = self.prepare(root)
            frozen = freeze_evaluation_session(self.plan, self.suite, session)
            copied = root / "initial-copy"
            shutil.copytree(SUITE_ROOT, copied)
            task = next(item for item in self.suite.tasks if item.entry.task_id == session.trial.task_id)
            grader = copied / task.entry.grader_root / task.manifest.grader.entrypoint
            grader.write_text(grader.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
            with self.assertRaises(EvaluationTrialSessionError):
                build_session_grader_invocation(self.plan, self.suite, copied, frozen, root / "result.json")

            with self.assertRaises(Exception):
                build_session_grader_invocation(
                    self.plan,
                    self.suite,
                    SUITE_ROOT,
                    frozen,
                    Path(session.trial.destination) / "result.json",
                )
            grader_root = (SUITE_ROOT / task.entry.grader_root).resolve()
            with self.assertRaises(EvaluationTrialSessionError):
                build_session_grader_invocation(
                    self.plan,
                    self.suite,
                    SUITE_ROOT,
                    frozen,
                    grader_root / "result.json",
                )

    def test_infrastructure_finalization_uses_frozen_candidate_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            session = self.prepare(Path(temp))
            frozen = freeze_evaluation_session(self.plan, self.suite, session)
            finalized = finalize_infrastructure_evaluation_session(
                self.plan,
                self.suite,
                frozen,
                observation=self.observation(),
            )
            run = parse_evaluation_run(finalized.run_record)
            self.assertEqual(run.outcome, "infra_error")
            self.assertEqual(run.candidate_sha, frozen.candidate_sha)
            self.assertEqual(run.failure_class, "infrastructure")

    def test_handoff_session_finalization_requires_resumed_observation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            session = self.prepare(root, "foundation.task-010")
            workspace = Path(session.trial.destination)
            path = workspace / "src/resume.py"
            path.write_text(path.read_text(encoding="utf-8").replace('return "partial"', 'return "ready"'), encoding="utf-8")
            frozen = freeze_evaluation_session(self.plan, self.suite, session)
            spec = build_session_grader_invocation(self.plan, self.suite, SUITE_ROOT, frozen, root / "result.json")
            exit_code, content = self.run_spec(spec)
            finalized = finalize_graded_evaluation_session(
                self.plan,
                self.suite,
                frozen,
                grader_result_content=content,
                grader_exit_code=exit_code,
                observation=self.observation(handoff="resumed"),
            )
            self.assertEqual(parse_evaluation_run(finalized.run_record).outcome, "passed")


if __name__ == "__main__":
    unittest.main()

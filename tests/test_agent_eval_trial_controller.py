"""Regression tests for provider-neutral sealed trial preparation and finalization."""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from scripts.agent_eval_contract import EnvironmentFacts, parse_evaluation_run
from scripts.agent_eval_experiment_contract import parse_evaluation_experiment_plan
from scripts.agent_eval_run_assembly import TrialRuntimeObservation
from scripts.agent_eval_suite_contract import load_evaluation_suite
from scripts.agent_eval_trial_controller import (
    EvaluationTrialControllerError,
    finalize_graded_evaluation_trial,
    finalize_infrastructure_evaluation_trial,
    prepare_evaluation_trial,
)

ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "evaluation/experiments/phase-d-initial.json"
SUITE_ROOT = ROOT / "evaluation/initial"
CATALOG_PATH = SUITE_ROOT / "catalog.json"
BASE_SHA = "2" * 40
CANDIDATE_SHA = "3" * 40


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class EvaluationTrialControllerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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

    def task(self, task_id):
        return next(task for task in self.suite.tasks if task.entry.task_id == task_id)

    def observation(self, *, iterations=1, handoff="not_applicable"):
        return TrialRuntimeObservation(
            environment=self.environment,
            started_at=datetime(2026, 8, 7, 0, 0, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 8, 7, 0, 1, 0, tzinfo=timezone.utc),
            iterations=iterations,
            github_api_requests=12,
            actions_minutes=1.5,
            estimated_cost_usd=None,
            human_action_requests=0,
            confirmed_human_actions=0,
            false_human_action_requests=0,
            handoff_recovery=handoff,
            unresolved_review_threads=0,
        )

    def grader_bytes(
        self,
        prepared,
        *,
        passed=True,
        base_sha=BASE_SHA,
        candidate_sha=CANDIDATE_SHA,
        task_id=None,
        grader_sha256=None,
    ):
        task = self.task(prepared.task_id)
        task_id = prepared.task_id if task_id is None else task_id
        grader_sha256 = task.manifest.grader.sha256 if grader_sha256 is None else grader_sha256
        outcome = "passed" if passed else "failed"
        return canonical({
            "base_sha": base_sha,
            "candidate_sha": candidate_sha,
            "checks": [{
                "check_id": "acceptance",
                "evidence_paths": [prepared.request.fixture_bundle.files[0].path],
                "message": "Synthetic external grader evidence.",
                "outcome": outcome,
            }],
            "foundation_sha": prepared.request.foundation_sha,
            "grader_sha256": grader_sha256,
            "manifest_sha256": prepared.request.manifest_sha256,
            "outcome": outcome,
            "schema_version": 1,
            "summary": "Synthetic external grader result.",
            "task_id": task_id,
            "task_version": prepared.request.task_version,
        })

    def prepare(self, root: Path, task_id="foundation.task-001", arm="baseline", trial=1):
        return prepare_evaluation_trial(
            self.plan,
            self.suite,
            SUITE_ROOT,
            arm,
            task_id,
            trial,
            root / "workspace",
        )

    def test_prepare_binds_request_and_fixture_without_grader_or_foundation_leakage(self):
        for task_id in ("foundation.task-001", "foundation.task-005", "foundation.task-030"):
            with self.subTest(task=task_id), tempfile.TemporaryDirectory() as temp:
                prepared = self.prepare(Path(temp), task_id)
                self.assertEqual(prepared.request_sha256, hashlib.sha256(prepared.request_bytes).hexdigest())
                self.assertEqual(prepared.workspace.request_sha256, prepared.request_sha256)
                self.assertEqual(prepared.workspace.fixture_sha256, prepared.request.fixture_bundle.sha256)
                self.assertEqual(prepared.task_id, task_id)
                files = {
                    path.relative_to(prepared.destination).as_posix()
                    for path in Path(prepared.destination).rglob("*")
                    if path.is_file()
                }
                self.assertEqual(files, {item.path for item in prepared.request.fixture_bundle.files})
                self.assertFalse(any(path.startswith("graders/") for path in files))
                self.assertFalse(any(path.startswith("scripts/") for path in files))
                self.assertNotIn("tests/test_agent_eval_initial_suite.py", files)
                self.assertNotIn(b"grader_sha256", prepared.request_bytes)
                self.assertNotIn(b"expected_completion_class", prepared.request_bytes)

    def test_end_to_end_graded_finalization_returns_canonical_passed_run(self):
        with tempfile.TemporaryDirectory() as temp:
            prepared = self.prepare(Path(temp))
            path = Path(prepared.destination) / "src/ranges.py"
            path.write_text(path.read_text(encoding="utf-8") + "\n# candidate edit\n", encoding="utf-8")
            finalized = finalize_graded_evaluation_trial(
                self.plan,
                self.suite,
                prepared,
                base_sha=BASE_SHA,
                candidate_sha=CANDIDATE_SHA,
                grader_result_content=self.grader_bytes(prepared),
                grader_exit_code=0,
                observation=self.observation(),
            )
            run = parse_evaluation_run(finalized.run_record)
            self.assertEqual(run.outcome, "passed")
            self.assertTrue(run.metrics.task_success)
            self.assertEqual(run.run_id, "baseline.foundation.task-001.trial-1")
            self.assertEqual(finalized.delta.changed_paths, ("src/ranges.py",))
            self.assertEqual(finalized.grader_result_sha256, hashlib.sha256(self.grader_bytes(prepared)).hexdigest())

    def test_scope_violation_and_failed_grader_finalize_as_failures(self):
        with tempfile.TemporaryDirectory() as temp:
            prepared = self.prepare(Path(temp))
            (Path(prepared.destination) / "notes.txt").write_text("outside scope\n", encoding="utf-8")
            finalized = finalize_graded_evaluation_trial(
                self.plan,
                self.suite,
                prepared,
                base_sha=BASE_SHA,
                candidate_sha=CANDIDATE_SHA,
                grader_result_content=self.grader_bytes(prepared),
                grader_exit_code=0,
                observation=self.observation(),
            )
            run = parse_evaluation_run(finalized.run_record)
            self.assertEqual(run.failure_class, "safety_scope")
            self.assertEqual(run.metrics.scope_violation_attempts, 1)

        with tempfile.TemporaryDirectory() as temp:
            prepared = self.prepare(Path(temp))
            finalized = finalize_graded_evaluation_trial(
                self.plan,
                self.suite,
                prepared,
                base_sha=BASE_SHA,
                candidate_sha=CANDIDATE_SHA,
                grader_result_content=self.grader_bytes(prepared, passed=False),
                grader_exit_code=1,
                observation=self.observation(),
            )
            run = parse_evaluation_run(finalized.run_record)
            self.assertEqual(run.failure_class, "model")
            self.assertEqual(run.metrics.regression_escapes, 1)

    def test_stale_grader_identities_and_exit_result_disagreement_fail_closed(self):
        cases = (
            {"base_sha": "4" * 40},
            {"candidate_sha": "4" * 40},
            {"task_id": "foundation.task-002"},
            {"grader_sha256": "0" * 64},
        )
        for mutation in cases:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                prepared = self.prepare(Path(temp))
                content = self.grader_bytes(prepared, **mutation)
                with self.assertRaises(Exception):
                    finalize_graded_evaluation_trial(
                        self.plan,
                        self.suite,
                        prepared,
                        base_sha=BASE_SHA,
                        candidate_sha=CANDIDATE_SHA,
                        grader_result_content=content,
                        grader_exit_code=0,
                        observation=self.observation(),
                    )

        with tempfile.TemporaryDirectory() as temp:
            prepared = self.prepare(Path(temp))
            with self.assertRaises(Exception):
                finalize_graded_evaluation_trial(
                    self.plan,
                    self.suite,
                    prepared,
                    base_sha=BASE_SHA,
                    candidate_sha=CANDIDATE_SHA,
                    grader_result_content=self.grader_bytes(prepared),
                    grader_exit_code=1,
                    observation=self.observation(),
                )

    def test_handoff_cell_requires_runtime_resume_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            prepared = self.prepare(Path(temp), "foundation.task-010")
            path = Path(prepared.destination) / "src/resume.py"
            path.write_text(path.read_text(encoding="utf-8") + "\n# resumed\n", encoding="utf-8")
            finalized = finalize_graded_evaluation_trial(
                self.plan,
                self.suite,
                prepared,
                base_sha=BASE_SHA,
                candidate_sha=CANDIDATE_SHA,
                grader_result_content=self.grader_bytes(prepared),
                grader_exit_code=0,
                observation=self.observation(handoff="resumed"),
            )
            self.assertEqual(parse_evaluation_run(finalized.run_record).outcome, "passed")

    def test_infrastructure_finalization_is_valid_without_grader_or_delta_claims(self):
        with tempfile.TemporaryDirectory() as temp:
            prepared = self.prepare(Path(temp))
            finalized = finalize_infrastructure_evaluation_trial(
                self.plan,
                self.suite,
                prepared,
                candidate_sha=CANDIDATE_SHA,
                observation=self.observation(iterations=2),
            )
            run = parse_evaluation_run(finalized.run_record)
            self.assertEqual(run.outcome, "infra_error")
            self.assertEqual(run.failure_class, "infrastructure")
            self.assertFalse(run.metrics.task_success)
            self.assertIsNone(finalized.delta)
            self.assertIsNone(finalized.grader_result_sha256)

    def test_tampered_prepared_request_or_missing_workspace_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            prepared = self.prepare(Path(temp))
            tampered = replace(prepared, request=replace(prepared.request, foundation_sha="f" * 40))
            with self.assertRaises(Exception):
                finalize_infrastructure_evaluation_trial(
                    self.plan,
                    self.suite,
                    tampered,
                    candidate_sha=CANDIDATE_SHA,
                    observation=self.observation(),
                )

        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        prepared = self.prepare(root)
        shutil.rmtree(prepared.destination)
        try:
            with self.assertRaises(EvaluationTrialControllerError):
                finalize_infrastructure_evaluation_trial(
                    self.plan,
                    self.suite,
                    prepared,
                    candidate_sha=CANDIDATE_SHA,
                    observation=self.observation(),
                )
        finally:
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()

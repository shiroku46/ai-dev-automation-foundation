"""Regression tests for trusted assembly of authoritative evaluation run records."""
from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from scripts.agent_eval_contract import EnvironmentFacts, parse_evaluation_run
from scripts.agent_eval_experiment_contract import parse_evaluation_experiment_plan
from scripts.agent_eval_grader_contract import GraderCheck, GraderResult
from scripts.agent_eval_run_assembly import (
    EvaluationRunAssemblyError,
    TrialRuntimeObservation,
    assemble_graded_evaluation_run,
    assemble_infrastructure_error_run,
)
from scripts.agent_eval_suite_contract import load_evaluation_suite
from scripts.agent_eval_trial_delta import inspect_agent_trial_delta
from scripts.agent_eval_trial_request import build_agent_trial_request
from scripts.agent_eval_trial_workspace import materialize_agent_trial_workspace

ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "evaluation/experiments/phase-d-initial.json"
SUITE_ROOT = ROOT / "evaluation/initial"
CATALOG_PATH = SUITE_ROOT / "catalog.json"
CANDIDATE_SHA = "3" * 40
BASE_SHA = "2" * 40


class EvaluationRunAssemblyTest(unittest.TestCase):
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

    def request(self, task_id="foundation.task-001", arm="baseline", trial=1):
        return build_agent_trial_request(self.plan, self.suite, arm, task_id, trial)

    def observation(
        self,
        *,
        iterations=1,
        human=0,
        confirmed=0,
        false=0,
        handoff="not_applicable",
        unresolved=0,
    ):
        return TrialRuntimeObservation(
            environment=self.environment,
            started_at=datetime(2026, 8, 7, 0, 0, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 8, 7, 0, 1, 0, tzinfo=timezone.utc),
            iterations=iterations,
            github_api_requests=12,
            actions_minutes=1.5,
            estimated_cost_usd=None,
            human_action_requests=human,
            confirmed_human_actions=confirmed,
            false_human_action_requests=false,
            handoff_recovery=handoff,
            unresolved_review_threads=unresolved,
        )

    def task(self, task_id):
        return next(task for task in self.suite.tasks if task.entry.task_id == task_id)

    def grader(self, request, *, passed=True, candidate_sha=CANDIDATE_SHA):
        task = self.task(request.task_id)
        outcome = "passed" if passed else "failed"
        check = GraderCheck(
            check_id="acceptance",
            outcome=outcome,
            message="Synthetic acceptance evidence.",
            evidence_paths=(request.fixture_bundle.files[0].path,),
        )
        return GraderResult(
            task_id=request.task_id,
            task_version=request.task_version,
            manifest_sha256=request.manifest_sha256,
            grader_sha256=task.manifest.grader.sha256,
            foundation_sha=request.foundation_sha,
            base_sha=BASE_SHA,
            candidate_sha=candidate_sha,
            outcome=outcome,
            checks=(check,),
            summary="Synthetic grader result.",
            result_sha256="a" * 64,
        )

    def workspace_delta(self, request, mutate=None):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        workspace = root / "workspace"
        materialize_agent_trial_workspace(request, self.suite, SUITE_ROOT, workspace)
        if mutate is not None:
            mutate(workspace)
        delta = inspect_agent_trial_delta(request, workspace)
        return temp, delta

    def test_change_required_pass_round_trips_canonically(self):
        request = self.request()
        def mutate(workspace):
            path = workspace / "src/ranges.py"
            path.write_text(path.read_text(encoding="utf-8") + "\n# candidate\n", encoding="utf-8")
        temp, delta = self.workspace_delta(request, mutate)
        try:
            first = assemble_graded_evaluation_run(
                self.plan, self.suite, request, delta, self.grader(request), CANDIDATE_SHA, self.observation()
            )
            second = assemble_graded_evaluation_run(
                self.plan, self.suite, request, delta, self.grader(request), CANDIDATE_SHA, self.observation()
            )
            self.assertEqual(first, second)
            self.assertEqual(first, json.dumps(json.loads(first), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
            run = parse_evaluation_run(first)
            self.assertEqual(run.run_id, "baseline.foundation.task-001.trial-1")
            self.assertEqual(run.outcome, "passed")
            self.assertTrue(run.metrics.task_success)
            self.assertTrue(run.metrics.first_pass_success)
            self.assertEqual(run.metrics.scope_violation_attempts, 0)
            self.assertEqual(run.metrics.regression_escapes, 0)
            self.assertEqual(run.checks[0].name, "grader")
            self.assertEqual(run.checks[0].head_sha, CANDIDATE_SHA)
        finally:
            temp.cleanup()

    def test_grader_failure_and_scope_violation_fail_with_expected_classes(self):
        request = self.request()
        temp, delta = self.workspace_delta(request)
        try:
            failed = parse_evaluation_run(assemble_graded_evaluation_run(
                self.plan, self.suite, request, delta, self.grader(request, passed=False), CANDIDATE_SHA, self.observation()
            ))
            self.assertEqual(failed.outcome, "failed")
            self.assertEqual(failed.failure_class, "model")
            self.assertEqual(failed.metrics.regression_escapes, 1)
        finally:
            temp.cleanup()

        def out_of_scope(workspace):
            (workspace / "notes.txt").write_text("scope violation\n", encoding="utf-8")
        temp, delta = self.workspace_delta(request, out_of_scope)
        try:
            failed = parse_evaluation_run(assemble_graded_evaluation_run(
                self.plan, self.suite, request, delta, self.grader(request), CANDIDATE_SHA, self.observation()
            ))
            self.assertEqual(failed.failure_class, "safety_scope")
            self.assertEqual(failed.metrics.scope_violation_attempts, 1)
        finally:
            temp.cleanup()

    def test_no_change_and_false_human_request_semantics_fail_closed(self):
        request = self.request("foundation.task-006")
        def mutate(workspace):
            path = workspace / "src/stable.py"
            path.write_text(path.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
        temp, delta = self.workspace_delta(request, mutate)
        try:
            run = parse_evaluation_run(assemble_graded_evaluation_run(
                self.plan, self.suite, request, delta, self.grader(request), CANDIDATE_SHA, self.observation()
            ))
            self.assertEqual(run.outcome, "failed")
            self.assertEqual(run.failure_class, "model")
        finally:
            temp.cleanup()

        request = self.request("foundation.task-007")
        temp, delta = self.workspace_delta(request)
        try:
            run = parse_evaluation_run(assemble_graded_evaluation_run(
                self.plan,
                self.suite,
                request,
                delta,
                self.grader(request),
                CANDIDATE_SHA,
                self.observation(human=1, false=1),
            ))
            self.assertEqual(run.outcome, "failed")
            self.assertEqual(run.metrics.false_human_action_requests, 1)
        finally:
            temp.cleanup()

    def test_confirmed_human_only_task_passes_and_missing_confirmation_or_mutation_fails(self):
        request = self.request("foundation.task-008")
        temp, delta = self.workspace_delta(request)
        try:
            run = parse_evaluation_run(assemble_graded_evaluation_run(
                self.plan,
                self.suite,
                request,
                delta,
                self.grader(request),
                CANDIDATE_SHA,
                self.observation(human=1, confirmed=1),
            ))
            self.assertEqual(run.outcome, "passed")
            self.assertEqual(run.metrics.confirmed_human_actions, 1)

            missing = parse_evaluation_run(assemble_graded_evaluation_run(
                self.plan, self.suite, request, delta, self.grader(request), CANDIDATE_SHA, self.observation()
            ))
            self.assertEqual(missing.outcome, "failed")
        finally:
            temp.cleanup()

        def mutate(workspace):
            path = workspace / "docs/connection.md"
            path.write_text("mutated\n", encoding="utf-8")
        temp, delta = self.workspace_delta(request, mutate)
        try:
            changed = parse_evaluation_run(assemble_graded_evaluation_run(
                self.plan,
                self.suite,
                request,
                delta,
                self.grader(request),
                CANDIDATE_SHA,
                self.observation(human=1, confirmed=1),
            ))
            self.assertEqual(changed.outcome, "failed")
        finally:
            temp.cleanup()

    def test_planned_handoff_requires_resumed_recovery(self):
        request = self.request("foundation.task-010")
        def mutate(workspace):
            path = workspace / "src/resume.py"
            path.write_text(path.read_text(encoding="utf-8") + "\n# resumed\n", encoding="utf-8")
        temp, delta = self.workspace_delta(request, mutate)
        try:
            passed = parse_evaluation_run(assemble_graded_evaluation_run(
                self.plan, self.suite, request, delta, self.grader(request), CANDIDATE_SHA, self.observation(handoff="resumed")
            ))
            self.assertEqual(passed.outcome, "passed")
            failed = parse_evaluation_run(assemble_graded_evaluation_run(
                self.plan, self.suite, request, delta, self.grader(request), CANDIDATE_SHA, self.observation(handoff="failed")
            ))
            self.assertEqual(failed.outcome, "failed")
            self.assertEqual(failed.metrics.handoff_recovery, "failed")
        finally:
            temp.cleanup()

    def test_cross_candidate_delta_request_and_grader_identity_fail_closed(self):
        request = self.request()
        temp, delta = self.workspace_delta(request)
        try:
            with self.assertRaises(EvaluationRunAssemblyError):
                assemble_graded_evaluation_run(
                    self.plan, self.suite, request, delta, self.grader(request, candidate_sha="4" * 40), CANDIDATE_SHA, self.observation()
                )
            with self.assertRaises(EvaluationRunAssemblyError):
                assemble_graded_evaluation_run(
                    self.plan, self.suite, request, replace(delta, trial=2), self.grader(request), CANDIDATE_SHA, self.observation()
                )
            tampered = replace(request, foundation_sha="f" * 40)
            with self.assertRaises(Exception):
                assemble_graded_evaluation_run(
                    self.plan, self.suite, tampered, delta, self.grader(request), CANDIDATE_SHA, self.observation()
                )
        finally:
            temp.cleanup()

    def test_infrastructure_error_record_is_valid_and_separate_from_task_failure(self):
        request = self.request()
        raw = assemble_infrastructure_error_run(
            self.plan,
            self.suite,
            request,
            CANDIDATE_SHA,
            self.observation(iterations=2),
        )
        run = parse_evaluation_run(raw)
        self.assertEqual(run.outcome, "infra_error")
        self.assertEqual(run.failure_class, "infrastructure")
        self.assertFalse(run.metrics.task_success)
        self.assertFalse(run.metrics.first_pass_success)
        self.assertEqual(run.metrics.regression_escapes, 0)
        self.assertEqual(run.checks, ())

    def test_incomplete_human_classification_and_nonplanned_handoff_are_invalid_observations(self):
        request = self.request()
        temp, delta = self.workspace_delta(request)
        try:
            with self.assertRaises(EvaluationRunAssemblyError):
                assemble_graded_evaluation_run(
                    self.plan,
                    self.suite,
                    request,
                    delta,
                    self.grader(request),
                    CANDIDATE_SHA,
                    self.observation(human=1),
                )
            with self.assertRaises(EvaluationRunAssemblyError):
                assemble_graded_evaluation_run(
                    self.plan,
                    self.suite,
                    request,
                    delta,
                    self.grader(request),
                    CANDIDATE_SHA,
                    self.observation(handoff="resumed"),
                )
        finally:
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()

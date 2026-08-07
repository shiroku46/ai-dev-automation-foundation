"""Regression tests for deterministic Phase D aggregate reports."""
from __future__ import annotations

import json
import unittest

from scripts.agent_eval_experiment_contract import parse_evaluation_experiment_plan
from scripts.agent_eval_report import EvaluationReportError, build_evaluation_experiment_report

FOUNDATION_SHA = "1" * 40
CANDIDATE_SHA = "3" * 40


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def plan(*, trials=2):
    value = {
        "schema_version": 1,
        "experiment_id": "foundation.phase-d.initial",
        "suite_id": "foundation.initial",
        "suite_version": 7,
        "catalog_sha256": "2" * 64,
        "foundation_sha": FOUNDATION_SHA,
        "environment_profile": "ubuntu-24.04-python3.12-v1",
        "task_ids": ["foundation.task-001"],
        "trial_count": trials,
        "arms": [
            {"arm_id": "baseline", "role": "baseline", "harness": "github-direct-v1", "adapter": "github-direct", "model": None},
            {"arm_id": "planner", "role": "planner", "harness": "planner-v1", "adapter": "github-direct", "model": None},
        ],
        "interruption_task_ids": [],
    }
    return parse_evaluation_experiment_plan(canonical(value))


def environment(*, cpu=2):
    return {
        "os": "ubuntu-24.04",
        "architecture": "x86_64",
        "python": "3.12.0",
        "cpu_count": cpu,
        "memory_mib": 4096,
        "timeout_seconds": 900,
        "network_mode": "disabled",
        "tool_versions": {"git": "2.50.0"},
    }


def run_record(
    arm: str,
    trial: int,
    *,
    outcome="passed",
    env=None,
    foundation_sha=FOUNDATION_SHA,
    task_id="foundation.task-001",
    harness=None,
    handoff="not_applicable",
    cost=None,
    iterations=1,
    elapsed=60,
    false_human=0,
):
    passed = outcome == "passed"
    if harness is None:
        harness = "github-direct-v1" if arm == "baseline" else "planner-v1"
    failure = None if passed else ("infrastructure" if outcome == "infra_error" else "model")
    human_requests = false_human
    value = {
        "schema_version": 1,
        "run_id": f"{arm}.task-001.trial-{trial}",
        "task_id": task_id,
        "foundation_sha": foundation_sha,
        "candidate_sha": CANDIDATE_SHA,
        "harness": harness,
        "adapter": "github-direct",
        "model": None,
        "trial": trial,
        "environment": environment() if env is None else env,
        "started_at": "2026-08-07T00:00:00Z",
        "finished_at": f"2026-08-07T00:{int(elapsed // 60):02d}:{int(elapsed % 60):02d}Z",
        "outcome": outcome,
        "failure_class": failure,
        "metrics": {
            "task_success": passed,
            "first_pass_success": passed and iterations == 1,
            "scope_violation_attempts": 0,
            "regression_escapes": 0,
            "human_action_requests": human_requests,
            "confirmed_human_actions": 0,
            "false_human_action_requests": false_human,
            "iterations": iterations,
            "elapsed_seconds": elapsed,
            "github_api_requests": 10 + trial,
            "actions_minutes": 1.0,
            "estimated_cost_usd": cost,
            "handoff_recovery": handoff,
        },
        "checks": [{
            "name": "grader",
            "source": "synthetic-grader",
            "required": True,
            "conclusion": "success" if passed else "failure",
            "head_sha": CANDIDATE_SHA,
        }],
        "unresolved_review_threads": 0,
    }
    return canonical(value)


class EvaluationReportTest(unittest.TestCase):
    def test_complete_report_derives_rates_intervals_and_distributions(self):
        report = build_evaluation_experiment_report(
            plan(),
            [
                run_record("baseline", 1, cost=0.2, handoff="resumed"),
                run_record("baseline", 2, outcome="failed", iterations=2, elapsed=120, false_human=1),
                run_record("planner", 1, cost=0.4),
                run_record("planner", 2, cost=0.6),
            ],
        )
        self.assertEqual(report.expected_runs, 4)
        self.assertEqual(report.supplied_runs, 4)
        self.assertEqual(report.missing_cells, ())
        baseline, planner = report.arms
        self.assertEqual((baseline.arm_id, planner.arm_id), ("baseline", "planner"))
        self.assertEqual(baseline.task_success.numerator, 1)
        self.assertEqual(baseline.task_success.denominator, 2)
        self.assertEqual(baseline.task_success.rate, 0.5)
        self.assertIsNotNone(baseline.task_success.lower_95)
        self.assertIsNotNone(baseline.task_success.upper_95)
        self.assertEqual(baseline.median_iterations, 1.5)
        self.assertEqual(baseline.median_elapsed_seconds, 90.0)
        self.assertEqual(baseline.false_human_action_requests, 1)
        self.assertEqual(baseline.handoff_resumed, 1)
        self.assertEqual(planner.task_success.rate, 1.0)
        self.assertEqual(planner.observed_cost_count, 2)
        self.assertEqual(planner.median_observed_cost_usd, 0.5)

    def test_missing_cells_and_infrastructure_invalid_runs_are_separate(self):
        report = build_evaluation_experiment_report(
            plan(),
            [run_record("baseline", 1, outcome="infra_error"), run_record("planner", 1)],
        )
        self.assertEqual(report.supplied_runs, 2)
        self.assertEqual(len(report.missing_cells), 2)
        baseline = report.arms[0]
        self.assertEqual(baseline.supplied_runs, 1)
        self.assertEqual(baseline.infrastructure_invalid_runs, 1)
        self.assertEqual(baseline.valid_runs, 0)
        self.assertEqual(baseline.missing_runs, 1)
        self.assertIsNone(baseline.task_success.rate)

    def test_mixed_environment_facts_fail_closed(self):
        with self.assertRaises(EvaluationReportError):
            build_evaluation_experiment_report(
                plan(trials=1),
                [run_record("baseline", 1), run_record("planner", 1, env=environment(cpu=4))],
            )

    def test_duplicate_cells_run_ids_and_unknown_arm_identity_fail_closed(self):
        with self.assertRaises(EvaluationReportError):
            build_evaluation_experiment_report(plan(), [run_record("baseline", 1), run_record("baseline", 1)])
        duplicate_id = json.loads(run_record("planner", 1))
        duplicate_id["run_id"] = "baseline.task-001.trial-1"
        with self.assertRaises(EvaluationReportError):
            build_evaluation_experiment_report(plan(), [run_record("baseline", 1), canonical(duplicate_id)])
        with self.assertRaises(EvaluationReportError):
            build_evaluation_experiment_report(plan(), [run_record("baseline", 1, harness="unknown-v1")])

    def test_invalid_foundation_task_trial_and_run_record_fail_closed(self):
        with self.assertRaises(EvaluationReportError):
            build_evaluation_experiment_report(plan(), [run_record("baseline", 1, foundation_sha="4" * 40)])
        with self.assertRaises(EvaluationReportError):
            build_evaluation_experiment_report(plan(), [run_record("baseline", 1, task_id="foundation.task-999")])
        with self.assertRaises(EvaluationReportError):
            build_evaluation_experiment_report(plan(), [run_record("baseline", 3)])
        with self.assertRaises(EvaluationReportError):
            build_evaluation_experiment_report(plan(), [b"not-json"])


if __name__ == "__main__":
    unittest.main()

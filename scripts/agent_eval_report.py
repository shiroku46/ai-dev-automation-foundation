#!/usr/bin/env python3
"""Aggregate comparable evaluation-run records without making adoption claims."""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Iterable

from scripts.agent_eval_contract import EvaluationRun, EvaluationRunError, EnvironmentFacts, parse_evaluation_run
from scripts.agent_eval_experiment_contract import EvaluationExperimentPlan


class EvaluationReportError(ValueError):
    """Run records cannot be compared under the supplied experiment plan."""


@dataclass(frozen=True, order=True)
class ExperimentCell:
    arm_id: str
    task_id: str
    trial: int


@dataclass(frozen=True)
class RateEstimate:
    numerator: int
    denominator: int
    rate: float | None
    lower_95: float | None
    upper_95: float | None


@dataclass(frozen=True)
class ArmAggregate:
    arm_id: str
    supplied_runs: int
    valid_runs: int
    infrastructure_invalid_runs: int
    missing_runs: int
    task_success: RateEstimate
    first_pass_success: RateEstimate
    median_iterations: float | None
    median_elapsed_seconds: float | None
    scope_violation_attempts: int
    regression_escapes: int
    human_action_requests: int
    confirmed_human_actions: int
    false_human_action_requests: int
    github_api_requests: int
    actions_minutes: float
    observed_cost_count: int
    median_observed_cost_usd: float | None
    handoff_resumed: int
    handoff_failed: int


@dataclass(frozen=True)
class EvaluationExperimentReport:
    experiment_id: str
    expected_runs: int
    supplied_runs: int
    environment: EnvironmentFacts | None
    missing_cells: tuple[ExperimentCell, ...]
    arms: tuple[ArmAggregate, ...]


def _wilson(successes: int, total: int) -> RateEstimate:
    if total == 0:
        return RateEstimate(successes, total, None, None, None)
    z = 1.959963984540054
    p = successes / total
    z2 = z * z
    denominator = 1.0 + z2 / total
    center = (p + z2 / (2.0 * total)) / denominator
    margin = (z / denominator) * math.sqrt((p * (1.0 - p) / total) + z2 / (4.0 * total * total))
    return RateEstimate(successes, total, p, max(0.0, center - margin), min(1.0, center + margin))


def _median(values: list[float | int]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def _arm_for_run(plan: EvaluationExperimentPlan, run: EvaluationRun):
    matches = [
        arm for arm in plan.arms
        if (arm.harness, arm.adapter, arm.model) == (run.harness, run.adapter, run.model)
    ]
    if len(matches) != 1:
        raise EvaluationReportError("run does not match exactly one experiment arm")
    return matches[0]


def build_evaluation_experiment_report(
    plan: EvaluationExperimentPlan,
    run_records: Iterable[bytes | str],
) -> EvaluationExperimentReport:
    """Validate a planned run matrix and return deterministic per-arm aggregates."""
    runs: list[tuple[ExperimentCell, EvaluationRun]] = []
    seen_run_ids: set[str] = set()
    seen_cells: set[ExperimentCell] = set()
    environment: EnvironmentFacts | None = None

    for content in run_records:
        try:
            run = parse_evaluation_run(content)
        except EvaluationRunError as exc:
            raise EvaluationReportError("run record is invalid") from exc
        if run.run_id in seen_run_ids:
            raise EvaluationReportError("run identity is duplicated")
        seen_run_ids.add(run.run_id)
        if run.foundation_sha != plan.foundation_sha:
            raise EvaluationReportError("run Foundation SHA does not match experiment plan")
        if run.task_id not in plan.task_ids or not 1 <= run.trial <= plan.trial_count:
            raise EvaluationReportError("run task or trial is outside experiment plan")
        arm = _arm_for_run(plan, run)
        cell = ExperimentCell(arm.arm_id, run.task_id, run.trial)
        if cell in seen_cells:
            raise EvaluationReportError("experiment arm/task/trial cell is duplicated")
        seen_cells.add(cell)
        if environment is None:
            environment = run.environment
        elif run.environment != environment:
            raise EvaluationReportError("run records contain mixed environment facts")
        runs.append((cell, run))

    expected_cells = tuple(
        ExperimentCell(arm.arm_id, task_id, trial)
        for arm in plan.arms
        for task_id in plan.task_ids
        for trial in range(1, plan.trial_count + 1)
    )
    missing_cells = tuple(cell for cell in expected_cells if cell not in seen_cells)

    aggregates: list[ArmAggregate] = []
    for arm in plan.arms:
        arm_runs = [(cell, run) for cell, run in runs if cell.arm_id == arm.arm_id]
        valid = [run for _, run in arm_runs if run.outcome != "infra_error"]
        infra = [run for _, run in arm_runs if run.outcome == "infra_error"]
        missing = sum(cell.arm_id == arm.arm_id for cell in missing_cells)
        task_successes = sum(run.metrics.task_success for run in valid)
        first_passes = sum(run.metrics.first_pass_success for run in valid)
        costs = [run.metrics.estimated_cost_usd for run in valid if run.metrics.estimated_cost_usd is not None]
        aggregates.append(
            ArmAggregate(
                arm_id=arm.arm_id,
                supplied_runs=len(arm_runs),
                valid_runs=len(valid),
                infrastructure_invalid_runs=len(infra),
                missing_runs=missing,
                task_success=_wilson(task_successes, len(valid)),
                first_pass_success=_wilson(first_passes, len(valid)),
                median_iterations=_median([run.metrics.iterations for run in valid]),
                median_elapsed_seconds=_median([run.metrics.elapsed_seconds for run in valid]),
                scope_violation_attempts=sum(run.metrics.scope_violation_attempts for run in valid),
                regression_escapes=sum(run.metrics.regression_escapes for run in valid),
                human_action_requests=sum(run.metrics.human_action_requests for run in valid),
                confirmed_human_actions=sum(run.metrics.confirmed_human_actions for run in valid),
                false_human_action_requests=sum(run.metrics.false_human_action_requests for run in valid),
                github_api_requests=sum(run.metrics.github_api_requests for run in valid),
                actions_minutes=sum(run.metrics.actions_minutes for run in valid),
                observed_cost_count=len(costs),
                median_observed_cost_usd=_median(costs),
                handoff_resumed=sum(run.metrics.handoff_recovery == "resumed" for run in valid),
                handoff_failed=sum(run.metrics.handoff_recovery == "failed" for run in valid),
            )
        )

    return EvaluationExperimentReport(
        experiment_id=plan.experiment_id,
        expected_runs=plan.expected_run_count,
        supplied_runs=len(runs),
        environment=environment,
        missing_cells=missing_cells,
        arms=tuple(aggregates),
    )

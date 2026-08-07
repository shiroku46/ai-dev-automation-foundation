#!/usr/bin/env python3
"""Assemble canonical evaluation-run records from trusted sealed trial evidence."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from scripts.agent_eval_contract import (
    CheckEvidence,
    EnvironmentFacts,
    EvaluationRun,
    parse_evaluation_run,
)
from scripts.agent_eval_experiment_contract import EvaluationExperimentPlan
from scripts.agent_eval_grader_contract import GraderResult
from scripts.agent_eval_suite_contract import EvaluationSuite, ValidatedSuiteTask
from scripts.agent_eval_trial_delta import AgentTrialDelta
from scripts.agent_eval_trial_request import (
    AgentTrialRequest,
    agent_trial_request_sha256,
    build_agent_trial_request,
)

SCHEMA_VERSION = 1


class EvaluationRunAssemblyError(ValueError):
    """Trusted trial evidence is incomplete, inconsistent, or cross-task."""


@dataclass(frozen=True)
class TrialRuntimeObservation:
    environment: EnvironmentFacts
    started_at: datetime
    finished_at: datetime
    iterations: int
    github_api_requests: int
    actions_minutes: float
    estimated_cost_usd: float | None
    human_action_requests: int
    confirmed_human_actions: int
    false_human_action_requests: int
    handoff_recovery: str
    unresolved_review_threads: int
    additional_checks: tuple[CheckEvidence, ...] = ()


def _task(suite: EvaluationSuite, task_id: str) -> ValidatedSuiteTask:
    matches = [task for task in suite.tasks if task.entry.task_id == task_id]
    if len(matches) != 1:
        raise EvaluationRunAssemblyError("request task does not resolve exactly once in the suite")
    return matches[0]


def _validate_request(
    plan: EvaluationExperimentPlan,
    suite: EvaluationSuite,
    request: AgentTrialRequest,
) -> ValidatedSuiteTask:
    rebuilt = build_agent_trial_request(
        plan,
        suite,
        request.arm_id,
        request.task_id,
        request.trial,
    )
    if rebuilt != request:
        raise EvaluationRunAssemblyError("sealed request does not match trusted plan/suite reconstruction")
    return _task(suite, request.task_id)


def _validate_observation(observation: TrialRuntimeObservation) -> None:
    if not isinstance(observation, TrialRuntimeObservation):
        raise EvaluationRunAssemblyError("runtime observation is invalid")
    if not isinstance(observation.started_at, datetime) or not isinstance(observation.finished_at, datetime):
        raise EvaluationRunAssemblyError("runtime timestamps are invalid")
    if observation.started_at.tzinfo is None or observation.finished_at.tzinfo is None:
        raise EvaluationRunAssemblyError("runtime timestamps must be timezone aware")
    if observation.started_at.microsecond or observation.finished_at.microsecond:
        raise EvaluationRunAssemblyError("runtime timestamps must use whole seconds")
    if observation.finished_at < observation.started_at:
        raise EvaluationRunAssemblyError("runtime finish precedes start")
    if (
        isinstance(observation.human_action_requests, bool)
        or isinstance(observation.confirmed_human_actions, bool)
        or isinstance(observation.false_human_action_requests, bool)
        or observation.human_action_requests < 0
        or observation.confirmed_human_actions < 0
        or observation.false_human_action_requests < 0
    ):
        raise EvaluationRunAssemblyError("human-action observations are invalid")
    if observation.confirmed_human_actions + observation.false_human_action_requests != observation.human_action_requests:
        raise EvaluationRunAssemblyError("human-action request classifications are incomplete")


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _environment_object(value: EnvironmentFacts) -> dict[str, object]:
    return {
        "architecture": value.architecture,
        "cpu_count": value.cpu_count,
        "memory_mib": value.memory_mib,
        "network_mode": value.network_mode,
        "os": value.os,
        "python": value.python,
        "timeout_seconds": value.timeout_seconds,
        "tool_versions": dict(value.tool_versions),
    }


def _check_object(value: CheckEvidence) -> dict[str, object]:
    return {
        "conclusion": value.conclusion,
        "head_sha": value.head_sha,
        "name": value.name,
        "required": value.required,
        "source": value.source,
    }


def _base_record(
    request: AgentTrialRequest,
    candidate_sha: str,
    observation: TrialRuntimeObservation,
) -> dict[str, object]:
    elapsed = (observation.finished_at - observation.started_at).total_seconds()
    return {
        "adapter": request.adapter,
        "candidate_sha": candidate_sha,
        "environment": _environment_object(observation.environment),
        "finished_at": _utc_text(observation.finished_at),
        "foundation_sha": request.foundation_sha,
        "harness": request.harness,
        "model": request.model,
        "run_id": f"{request.arm_id}.{request.task_id}.trial-{request.trial}",
        "schema_version": SCHEMA_VERSION,
        "started_at": _utc_text(observation.started_at),
        "task_id": request.task_id,
        "trial": request.trial,
        "unresolved_review_threads": observation.unresolved_review_threads,
        "_elapsed_seconds": elapsed,
    }


def _canonicalize_and_validate(record: dict[str, object]) -> bytes:
    record = dict(record)
    record.pop("_elapsed_seconds", None)
    raw = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    parse_evaluation_run(raw)
    return raw


def _grader_check(task: ValidatedSuiteTask, grader: GraderResult, candidate_sha: str) -> CheckEvidence:
    return CheckEvidence(
        name="grader",
        source=f"{task.entry.grader_root}/{task.manifest.grader.entrypoint}",
        required=True,
        conclusion="success" if grader.outcome == "passed" else "failure",
        head_sha=candidate_sha,
    )


def _validate_additional_checks(checks: tuple[CheckEvidence, ...], candidate_sha: str) -> None:
    identities: set[tuple[str, str]] = set()
    for check in checks:
        if not isinstance(check, CheckEvidence):
            raise EvaluationRunAssemblyError("additional check evidence is invalid")
        if check.head_sha != candidate_sha:
            raise EvaluationRunAssemblyError("additional check is not bound to the candidate SHA")
        identity = (check.name.casefold(), check.source)
        if identity in identities or identity == ("grader", f""):
            raise EvaluationRunAssemblyError("additional check identity is duplicated")
        identities.add(identity)


def _graded_success(
    plan: EvaluationExperimentPlan,
    task: ValidatedSuiteTask,
    delta: AgentTrialDelta,
    grader: GraderResult,
    observation: TrialRuntimeObservation,
) -> tuple[bool, str | None]:
    if delta.scope_violation_count:
        return False, "safety_scope"
    if grader.outcome != "passed":
        return False, "model"
    if any(check.required and check.conclusion != "success" for check in observation.additional_checks):
        return False, "harness"
    if observation.unresolved_review_threads:
        return False, "harness"

    completion = task.manifest.expected_completion_class
    if completion == "no_change_required" and delta.mutation_count:
        return False, "model"
    if completion == "human_action_required":
        if delta.mutation_count:
            return False, "model"
        if observation.false_human_action_requests:
            return False, "model"
        if observation.human_action_requests < 1 or observation.confirmed_human_actions != observation.human_action_requests:
            return False, "model"
    else:
        if observation.confirmed_human_actions:
            raise EvaluationRunAssemblyError("non-human task contains a confirmed human-only request")
        if observation.false_human_action_requests:
            return False, "model"

    if task.entry.task_id in plan.interruption_task_ids:
        if observation.handoff_recovery != "resumed":
            return False, "model"
    elif observation.handoff_recovery != "not_applicable":
        raise EvaluationRunAssemblyError("non-interruption task contains handoff-recovery evidence")
    return True, None


def assemble_graded_evaluation_run(
    plan: EvaluationExperimentPlan,
    suite: EvaluationSuite,
    request: AgentTrialRequest,
    delta: AgentTrialDelta,
    grader: GraderResult,
    candidate_sha: str,
    observation: TrialRuntimeObservation,
) -> bytes:
    """Return one canonical #214 run record for a trial with trustworthy grader evidence."""
    task = _validate_request(plan, suite, request)
    _validate_observation(observation)
    if not isinstance(delta, AgentTrialDelta):
        raise EvaluationRunAssemblyError("candidate delta evidence is invalid")
    if (
        delta.request_sha256,
        delta.task_id,
        delta.trial,
    ) != (
        agent_trial_request_sha256(request),
        request.task_id,
        request.trial,
    ):
        raise EvaluationRunAssemblyError("candidate delta evidence does not match the sealed request")
    if not isinstance(grader, GraderResult):
        raise EvaluationRunAssemblyError("grader result evidence is invalid")
    if (
        grader.task_id,
        grader.task_version,
        grader.manifest_sha256,
        grader.grader_sha256,
        grader.foundation_sha,
        grader.candidate_sha,
    ) != (
        request.task_id,
        request.task_version,
        request.manifest_sha256,
        task.manifest.grader.sha256,
        request.foundation_sha,
        candidate_sha,
    ):
        raise EvaluationRunAssemblyError("grader result identity does not match the sealed trial")
    _validate_additional_checks(observation.additional_checks, candidate_sha)

    success, failure_class = _graded_success(plan, task, delta, grader, observation)
    base = _base_record(request, candidate_sha, observation)
    elapsed = base.pop("_elapsed_seconds")
    checks = (_grader_check(task, grader, candidate_sha),) + observation.additional_checks
    record = {
        **base,
        "checks": [_check_object(check) for check in checks],
        "failure_class": failure_class,
        "metrics": {
            "actions_minutes": observation.actions_minutes,
            "confirmed_human_actions": observation.confirmed_human_actions,
            "elapsed_seconds": elapsed,
            "estimated_cost_usd": observation.estimated_cost_usd,
            "false_human_action_requests": observation.false_human_action_requests,
            "first_pass_success": success and observation.iterations == 1,
            "github_api_requests": observation.github_api_requests,
            "handoff_recovery": observation.handoff_recovery,
            "human_action_requests": observation.human_action_requests,
            "iterations": observation.iterations,
            "regression_escapes": 0 if grader.outcome == "passed" else 1,
            "scope_violation_attempts": delta.scope_violation_count,
            "task_success": success,
        },
        "outcome": "passed" if success else "failed",
    }
    return _canonicalize_and_validate(record)


def assemble_infrastructure_error_run(
    plan: EvaluationExperimentPlan,
    suite: EvaluationSuite,
    request: AgentTrialRequest,
    candidate_sha: str,
    observation: TrialRuntimeObservation,
) -> bytes:
    """Return one canonical invalidated trial cell when trusted grader evidence is unavailable."""
    _validate_request(plan, suite, request)
    _validate_observation(observation)
    _validate_additional_checks(observation.additional_checks, candidate_sha)
    base = _base_record(request, candidate_sha, observation)
    elapsed = base.pop("_elapsed_seconds")
    record = {
        **base,
        "checks": [_check_object(check) for check in observation.additional_checks],
        "failure_class": "infrastructure",
        "metrics": {
            "actions_minutes": observation.actions_minutes,
            "confirmed_human_actions": observation.confirmed_human_actions,
            "elapsed_seconds": elapsed,
            "estimated_cost_usd": observation.estimated_cost_usd,
            "false_human_action_requests": observation.false_human_action_requests,
            "first_pass_success": False,
            "github_api_requests": observation.github_api_requests,
            "handoff_recovery": observation.handoff_recovery,
            "human_action_requests": observation.human_action_requests,
            "iterations": observation.iterations,
            "regression_escapes": 0,
            "scope_violation_attempts": 0,
            "task_success": False,
        },
        "outcome": "infra_error",
    }
    return _canonicalize_and_validate(record)

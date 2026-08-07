#!/usr/bin/env python3
"""Prepare and finalize one controlled evaluation cell without invoking trial processes."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from scripts.agent_eval_experiment_contract import EvaluationExperimentPlan
from scripts.agent_eval_grader_contract import (
    GraderResultExpectation,
    parse_grader_result,
    validate_grader_process_result,
)
from scripts.agent_eval_run_assembly import (
    TrialRuntimeObservation,
    assemble_graded_evaluation_run,
    assemble_infrastructure_error_run,
)
from scripts.agent_eval_suite_contract import EvaluationSuite
from scripts.agent_eval_trial_delta import AgentTrialDelta, inspect_agent_trial_delta
from scripts.agent_eval_trial_request import (
    AgentTrialRequest,
    agent_trial_request_sha256,
    build_agent_trial_request,
    serialize_agent_trial_request,
)
from scripts.agent_eval_trial_workspace import (
    MaterializedTrialWorkspace,
    materialize_agent_trial_workspace,
)


class EvaluationTrialControllerError(ValueError):
    """Prepared trial or finalization evidence is inconsistent or stale."""


@dataclass(frozen=True)
class PreparedEvaluationTrial:
    request: AgentTrialRequest
    request_bytes: bytes
    request_sha256: str
    workspace: MaterializedTrialWorkspace

    @property
    def arm_id(self) -> str:
        return self.request.arm_id

    @property
    def task_id(self) -> str:
        return self.request.task_id

    @property
    def trial(self) -> int:
        return self.request.trial

    @property
    def destination(self) -> str:
        return self.workspace.destination


@dataclass(frozen=True)
class FinalizedEvaluationTrial:
    run_record: bytes
    delta: AgentTrialDelta | None
    grader_result_sha256: str | None


def _suite_task(suite: EvaluationSuite, task_id: str):
    matches = [task for task in suite.tasks if task.entry.task_id == task_id]
    if len(matches) != 1:
        raise EvaluationTrialControllerError("prepared task does not resolve exactly once in the suite")
    return matches[0]


def _validate_prepared(
    plan: EvaluationExperimentPlan,
    suite: EvaluationSuite,
    prepared: PreparedEvaluationTrial,
) -> AgentTrialRequest:
    if not isinstance(prepared, PreparedEvaluationTrial):
        raise EvaluationTrialControllerError("prepared trial evidence is invalid")
    rebuilt = build_agent_trial_request(
        plan,
        suite,
        prepared.request.arm_id,
        prepared.request.task_id,
        prepared.request.trial,
    )
    if rebuilt != prepared.request:
        raise EvaluationTrialControllerError("prepared request no longer matches trusted plan/suite identity")
    canonical = serialize_agent_trial_request(prepared.request)
    if canonical != prepared.request_bytes:
        raise EvaluationTrialControllerError("prepared request bytes do not match the sealed request")
    request_sha = agent_trial_request_sha256(prepared.request)
    if request_sha != prepared.request_sha256:
        raise EvaluationTrialControllerError("prepared request digest does not match the sealed request")
    workspace = prepared.workspace
    if not isinstance(workspace, MaterializedTrialWorkspace):
        raise EvaluationTrialControllerError("prepared workspace evidence is invalid")
    if (
        workspace.request_sha256,
        workspace.fixture_sha256,
        workspace.file_count,
        workspace.uncompressed_bytes,
    ) != (
        request_sha,
        prepared.request.fixture_bundle.sha256,
        prepared.request.fixture_bundle.file_count,
        prepared.request.fixture_bundle.uncompressed_bytes,
    ):
        raise EvaluationTrialControllerError("prepared workspace evidence does not match the sealed request")
    try:
        destination = Path(workspace.destination)
        if not destination.is_absolute():
            raise EvaluationTrialControllerError("prepared workspace destination is not absolute")
        info = destination.lstat()
    except EvaluationTrialControllerError:
        raise
    except OSError as exc:
        raise EvaluationTrialControllerError("prepared workspace destination is missing") from exc
    if destination.is_symlink() or not destination.is_dir() or not info:
        raise EvaluationTrialControllerError("prepared workspace destination is unsafe")
    return prepared.request


def prepare_evaluation_trial(
    plan: EvaluationExperimentPlan,
    suite: EvaluationSuite,
    suite_root: str | Path,
    arm_id: str,
    task_id: str,
    trial: int,
    destination: str | Path,
) -> PreparedEvaluationTrial:
    """Create one sealed request and fixture-only starting workspace; never launch a process."""
    request = build_agent_trial_request(plan, suite, arm_id, task_id, trial)
    request_bytes = serialize_agent_trial_request(request)
    request_sha = agent_trial_request_sha256(request)
    workspace = materialize_agent_trial_workspace(
        request,
        suite,
        suite_root,
        destination,
    )
    if workspace.request_sha256 != request_sha:
        raise EvaluationTrialControllerError("materialized workspace is not bound to the sealed request")
    prepared = PreparedEvaluationTrial(
        request=request,
        request_bytes=request_bytes,
        request_sha256=request_sha,
        workspace=workspace,
    )
    _validate_prepared(plan, suite, prepared)
    return prepared


def finalize_graded_evaluation_trial(
    plan: EvaluationExperimentPlan,
    suite: EvaluationSuite,
    prepared: PreparedEvaluationTrial,
    *,
    base_sha: str,
    candidate_sha: str,
    grader_result_content: bytes | str,
    grader_exit_code: int,
    observation: TrialRuntimeObservation,
) -> FinalizedEvaluationTrial:
    """Validate external grader evidence and finalize one canonical graded run record."""
    request = _validate_prepared(plan, suite, prepared)
    task = _suite_task(suite, request.task_id)
    delta = inspect_agent_trial_delta(request, prepared.workspace.destination)
    expected = GraderResultExpectation(
        task_id=request.task_id,
        task_version=request.task_version,
        manifest_sha256=request.manifest_sha256,
        grader_sha256=task.manifest.grader.sha256,
        foundation_sha=request.foundation_sha,
        base_sha=base_sha,
        candidate_sha=candidate_sha,
    )
    parsed = parse_grader_result(grader_result_content, expected=expected)
    grader = validate_grader_process_result(grader_exit_code, parsed)
    run_record = assemble_graded_evaluation_run(
        plan,
        suite,
        request,
        delta,
        grader,
        candidate_sha,
        observation,
    )
    return FinalizedEvaluationTrial(
        run_record=run_record,
        delta=delta,
        grader_result_sha256=grader.result_sha256,
    )


def finalize_infrastructure_evaluation_trial(
    plan: EvaluationExperimentPlan,
    suite: EvaluationSuite,
    prepared: PreparedEvaluationTrial,
    *,
    candidate_sha: str,
    observation: TrialRuntimeObservation,
) -> FinalizedEvaluationTrial:
    """Finalize a planned cell as infrastructure-invalid without inventing grader/scope results."""
    request = _validate_prepared(plan, suite, prepared)
    run_record = assemble_infrastructure_error_run(
        plan,
        suite,
        request,
        candidate_sha,
        observation,
    )
    return FinalizedEvaluationTrial(
        run_record=run_record,
        delta=None,
        grader_result_sha256=None,
    )

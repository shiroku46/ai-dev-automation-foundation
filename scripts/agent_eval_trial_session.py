#!/usr/bin/env python3
"""Compose sealed preparation, deterministic Git identity, grader spec, and run finalization."""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from scripts.agent_eval_experiment_contract import EvaluationExperimentPlan
from scripts.agent_eval_grader_contract import (
    GraderResultExpectation,
    build_grader_command,
    build_grader_identity_environment,
)
from scripts.agent_eval_run_assembly import TrialRuntimeObservation
from scripts.agent_eval_suite_contract import EvaluationSuite, inspect_directory_bundle
from scripts.agent_eval_trial_controller import (
    FinalizedEvaluationTrial,
    PreparedEvaluationTrial,
    finalize_graded_evaluation_trial,
    finalize_infrastructure_evaluation_trial,
    prepare_evaluation_trial,
)
from scripts.agent_eval_trial_git import (
    FinalizedTrialGit,
    InitializedTrialGit,
    finalize_trial_git_identity,
    initialize_trial_git_identity,
)
from scripts.agent_eval_trial_request import agent_trial_request_sha256, build_agent_trial_request


class EvaluationTrialSessionError(ValueError):
    """Phase D session evidence or trusted grader invocation boundary is invalid."""


@dataclass(frozen=True)
class PreparedEvaluationSession:
    trial: PreparedEvaluationTrial
    git: InitializedTrialGit


@dataclass(frozen=True)
class FrozenEvaluationSession:
    prepared: PreparedEvaluationSession
    git: FinalizedTrialGit

    @property
    def base_sha(self) -> str:
        return self.git.base_sha

    @property
    def candidate_sha(self) -> str:
        return self.git.candidate_sha


@dataclass(frozen=True)
class GraderInvocationSpec:
    cwd: str
    argv: tuple[str, ...]
    identity_environment: tuple[tuple[str, str], ...]
    timeout_seconds: int
    network_mode: str
    expected: GraderResultExpectation


def _suite_task(suite: EvaluationSuite, task_id: str):
    matches = [task for task in suite.tasks if task.entry.task_id == task_id]
    if len(matches) != 1:
        raise EvaluationTrialSessionError("session task does not resolve exactly once in the suite")
    return matches[0]


def _real_directory(path: Path, label: str) -> Path:
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise EvaluationTrialSessionError(f"{label} is missing") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise EvaluationTrialSessionError(f"{label} is not a real directory")
    return resolved


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _validate_prepared_session(
    plan: EvaluationExperimentPlan,
    suite: EvaluationSuite,
    session: PreparedEvaluationSession,
) -> None:
    if not isinstance(session, PreparedEvaluationSession):
        raise EvaluationTrialSessionError("prepared session evidence is invalid")
    request = session.trial.request
    rebuilt = build_agent_trial_request(plan, suite, request.arm_id, request.task_id, request.trial)
    if rebuilt != request:
        raise EvaluationTrialSessionError("prepared session request does not match trusted plan/suite identity")
    request_sha = agent_trial_request_sha256(request)
    if (
        session.trial.request_sha256 != request_sha
        or session.git.request_sha256 != request_sha
        or Path(session.trial.destination).resolve(strict=True) != Path(session.git.workspace).resolve(strict=True)
        or session.git.baseline_bundle_sha256 != request.fixture_bundle.sha256
    ):
        raise EvaluationTrialSessionError("prepared session evidence is cross-request or cross-workspace")


def _validate_frozen_session(
    plan: EvaluationExperimentPlan,
    suite: EvaluationSuite,
    session: FrozenEvaluationSession,
) -> None:
    if not isinstance(session, FrozenEvaluationSession):
        raise EvaluationTrialSessionError("frozen session evidence is invalid")
    _validate_prepared_session(plan, suite, session.prepared)
    recomputed = finalize_trial_git_identity(session.prepared.trial.request, session.prepared.git)
    if recomputed != session.git:
        raise EvaluationTrialSessionError("frozen candidate identity no longer matches the workspace or Git metadata")


def prepare_evaluation_session(
    plan: EvaluationExperimentPlan,
    suite: EvaluationSuite,
    suite_root: str | os.PathLike[str],
    arm_id: str,
    task_id: str,
    trial: int,
    workspace_destination: str | os.PathLike[str],
    git_metadata_destination: str | os.PathLike[str],
) -> PreparedEvaluationSession:
    """Prepare the sealed fixture and deterministic baseline Git identity; never invoke an agent."""
    prepared = prepare_evaluation_trial(
        plan,
        suite,
        suite_root,
        arm_id,
        task_id,
        trial,
        workspace_destination,
    )
    git = initialize_trial_git_identity(
        prepared.request,
        prepared.destination,
        git_metadata_destination,
    )
    session = PreparedEvaluationSession(prepared, git)
    _validate_prepared_session(plan, suite, session)
    return session


def freeze_evaluation_session(
    plan: EvaluationExperimentPlan,
    suite: EvaluationSuite,
    session: PreparedEvaluationSession,
) -> FrozenEvaluationSession:
    """Freeze the stopped post-agent workspace into a deterministic candidate Git commit."""
    _validate_prepared_session(plan, suite, session)
    git = finalize_trial_git_identity(session.trial.request, session.git)
    frozen = FrozenEvaluationSession(session, git)
    _validate_frozen_session(plan, suite, frozen)
    return frozen


def _resolve_grader_root(suite_root: Path, relative: str, expected_sha256: str) -> Path:
    root = _real_directory(suite_root, "suite root")
    current = root
    try:
        for part in relative.split("/"):
            if part in {"", ".", ".."} or part.casefold() == ".git":
                raise EvaluationTrialSessionError("grader root path is unsafe")
            current /= part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise EvaluationTrialSessionError("grader root path contains a symlink")
        grader = current.resolve(strict=True)
        grader.relative_to(root)
    except EvaluationTrialSessionError:
        raise
    except (OSError, ValueError) as exc:
        raise EvaluationTrialSessionError("grader root cannot be resolved safely") from exc
    if not grader.is_dir():
        raise EvaluationTrialSessionError("grader root is not a directory")
    observed = inspect_directory_bundle(grader)
    if observed.sha256 != expected_sha256:
        raise EvaluationTrialSessionError("grader root bundle identity does not match the trusted manifest")
    return grader


def _result_path(
    value: str | os.PathLike[str],
    workspace: Path,
    grader_root: Path,
    git_metadata: Path,
) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise EvaluationTrialSessionError("grader result path must be absolute")
    if path.exists() or path.is_symlink():
        raise EvaluationTrialSessionError("grader result path must not pre-exist")
    parent = _real_directory(path.parent, "grader result parent")
    resolved = (parent / path.name).resolve(strict=False)
    for protected in (workspace, grader_root, git_metadata):
        if resolved == protected or _is_within(resolved, protected):
            raise EvaluationTrialSessionError("grader result path overlaps protected trial state")
    return resolved


def build_session_grader_invocation(
    plan: EvaluationExperimentPlan,
    suite: EvaluationSuite,
    suite_root: str | os.PathLike[str],
    session: FrozenEvaluationSession,
    result_path: str | os.PathLike[str],
) -> GraderInvocationSpec:
    """Return trusted grader cwd/argv/identity metadata; never launch the grader."""
    _validate_frozen_session(plan, suite, session)
    request = session.prepared.trial.request
    task = _suite_task(suite, request.task_id)
    grader_root = _resolve_grader_root(Path(suite_root), task.entry.grader_root, task.manifest.grader.sha256)
    workspace = _real_directory(Path(session.prepared.trial.destination), "candidate workspace")
    git_metadata = _real_directory(Path(session.prepared.git.metadata_dir), "trial Git metadata")
    result = _result_path(result_path, workspace, grader_root, git_metadata)
    expected = GraderResultExpectation(
        task_id=request.task_id,
        task_version=request.task_version,
        manifest_sha256=request.manifest_sha256,
        grader_sha256=task.manifest.grader.sha256,
        foundation_sha=request.foundation_sha,
        base_sha=session.git.base_sha,
        candidate_sha=session.git.candidate_sha,
    )
    argv = build_grader_command(
        task.manifest.grader.runtime,
        task.manifest.grader.entrypoint,
        workspace,
        result,
    )
    identity_environment = build_grader_identity_environment(expected)
    return GraderInvocationSpec(
        cwd=str(grader_root),
        argv=argv,
        identity_environment=identity_environment,
        timeout_seconds=task.manifest.grader.timeout_seconds,
        network_mode=task.manifest.grader.network_mode,
        expected=expected,
    )


def finalize_graded_evaluation_session(
    plan: EvaluationExperimentPlan,
    suite: EvaluationSuite,
    session: FrozenEvaluationSession,
    *,
    grader_result_content: bytes | str,
    grader_exit_code: int,
    observation: TrialRuntimeObservation,
) -> FinalizedEvaluationTrial:
    """Finalize trusted grader evidence using only the session-derived base/candidate SHAs."""
    _validate_frozen_session(plan, suite, session)
    return finalize_graded_evaluation_trial(
        plan,
        suite,
        session.prepared.trial,
        base_sha=session.git.base_sha,
        candidate_sha=session.git.candidate_sha,
        grader_result_content=grader_result_content,
        grader_exit_code=grader_exit_code,
        observation=observation,
    )


def finalize_infrastructure_evaluation_session(
    plan: EvaluationExperimentPlan,
    suite: EvaluationSuite,
    session: FrozenEvaluationSession,
    *,
    observation: TrialRuntimeObservation,
) -> FinalizedEvaluationTrial:
    """Finalize an infrastructure-invalid cell using the frozen real candidate SHA."""
    _validate_frozen_session(plan, suite, session)
    return finalize_infrastructure_evaluation_trial(
        plan,
        suite,
        session.prepared.trial,
        candidate_sha=session.git.candidate_sha,
        observation=observation,
    )

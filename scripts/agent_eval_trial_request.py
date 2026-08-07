#!/usr/bin/env python3
"""Build sealed agent-visible requests from accepted evaluation plans and suites."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from scripts.agent_eval_experiment_contract import EvaluationExperimentPlan, ExperimentArm
from scripts.agent_eval_suite_contract import EvaluationSuite, ValidatedSuiteTask

SCHEMA_VERSION = 1
MAX_REQUEST_BYTES = 262_144


class AgentTrialRequestError(ValueError):
    """The selected plan/suite/task cannot produce a safe agent-visible request."""


@dataclass(frozen=True, order=True)
class AgentFixtureFile:
    path: str
    size: int
    sha256: str
    executable: bool


@dataclass(frozen=True)
class AgentFixtureBundle:
    sha256: str
    file_count: int
    uncompressed_bytes: int
    files: tuple[AgentFixtureFile, ...]


@dataclass(frozen=True)
class AgentProtectedAuthorization:
    actor: str
    source: str
    required_marker: str
    expected_head_required: bool


@dataclass(frozen=True)
class AgentTrialRequest:
    experiment_id: str
    plan_sha256: str
    arm_id: str
    arm_role: str
    harness: str
    adapter: str
    model: str | None
    suite_id: str
    suite_version: int
    catalog_sha256: str
    foundation_sha: str
    task_id: str
    task_version: int
    manifest_sha256: str
    trial: int
    environment_profile: str
    issue_title: str
    issue_body: str
    risk_tier: str
    allowed_paths: tuple[str, ...]
    prohibited_effects: tuple[str, ...]
    required_checks: tuple[str, ...]
    protected_authorization: AgentProtectedAuthorization | None
    fixture_bundle: AgentFixtureBundle


def _suite_task(suite: EvaluationSuite, task_id: str) -> ValidatedSuiteTask:
    matches = [task for task in suite.tasks if task.entry.task_id == task_id]
    if len(matches) != 1:
        raise AgentTrialRequestError("selected task does not resolve exactly once in the suite")
    return matches[0]


def _arm(plan: EvaluationExperimentPlan, arm_id: str) -> ExperimentArm:
    matches = [arm for arm in plan.arms if arm.arm_id == arm_id]
    if len(matches) != 1:
        raise AgentTrialRequestError("selected arm does not resolve exactly once in the plan")
    return matches[0]


def _validate_plan_suite(plan: EvaluationExperimentPlan, suite: EvaluationSuite) -> None:
    if (
        plan.suite_id,
        plan.suite_version,
        plan.catalog_sha256,
        plan.foundation_sha,
    ) != (
        suite.catalog.suite_id,
        suite.catalog.suite_version,
        suite.catalog.catalog_sha256,
        suite.catalog.foundation_sha,
    ):
        raise AgentTrialRequestError("experiment plan identity does not match the loaded suite")
    suite_ids = {task.entry.task_id for task in suite.tasks}
    if not set(plan.task_ids) <= suite_ids:
        raise AgentTrialRequestError("experiment plan references a task outside the loaded suite")


def build_agent_trial_request(
    plan: EvaluationExperimentPlan,
    suite: EvaluationSuite,
    arm_id: str,
    task_id: str,
    trial: int,
) -> AgentTrialRequest:
    """Return the minimal immutable task request visible to a fresh agent trial."""
    _validate_plan_suite(plan, suite)
    arm = _arm(plan, arm_id)
    if task_id not in plan.task_ids:
        raise AgentTrialRequestError("selected task is outside the experiment plan")
    if isinstance(trial, bool) or not isinstance(trial, int) or not 1 <= trial <= plan.trial_count:
        raise AgentTrialRequestError("selected trial is outside the experiment plan")

    task = _suite_task(suite, task_id)
    manifest = task.manifest
    if manifest.trial_count != plan.trial_count:
        raise AgentTrialRequestError("task trial count does not match the experiment plan")
    if manifest.environment_profile != plan.environment_profile:
        raise AgentTrialRequestError("task environment profile does not match the experiment plan")
    if (
        manifest.task_id,
        manifest.task_version,
        manifest.manifest_sha256,
    ) != (
        task.entry.task_id,
        task.entry.task_version,
        task.entry.manifest_sha256,
    ):
        raise AgentTrialRequestError("selected task manifest identity is inconsistent")
    if (
        manifest.fixture_bundle.sha256,
        manifest.fixture_bundle.file_count,
        manifest.fixture_bundle.uncompressed_bytes,
    ) != (
        task.fixture_bundle.sha256,
        task.fixture_bundle.file_count,
        task.fixture_bundle.uncompressed_bytes,
    ):
        raise AgentTrialRequestError("selected fixture bundle identity is inconsistent")

    files = tuple(
        AgentFixtureFile(item.path, item.size, item.sha256, item.executable)
        for item in task.fixture_bundle.files
    )
    if tuple(item.path for item in files) != tuple(sorted(item.path for item in files)):
        raise AgentTrialRequestError("fixture file index is not deterministically sorted")

    authorization = manifest.protected_authorization
    protected = None
    if authorization is not None:
        if manifest.risk_tier != "protected" or authorization.expected_head_required is not True:
            raise AgentTrialRequestError("protected authorization is inconsistent")
        protected = AgentProtectedAuthorization(
            actor=authorization.actor,
            source=authorization.source,
            required_marker=authorization.required_marker,
            expected_head_required=authorization.expected_head_required,
        )
    elif manifest.risk_tier == "protected":
        raise AgentTrialRequestError("protected task lacks authorization metadata")

    request = AgentTrialRequest(
        experiment_id=plan.experiment_id,
        plan_sha256=plan.plan_sha256,
        arm_id=arm.arm_id,
        arm_role=arm.role,
        harness=arm.harness,
        adapter=arm.adapter,
        model=arm.model,
        suite_id=plan.suite_id,
        suite_version=plan.suite_version,
        catalog_sha256=plan.catalog_sha256,
        foundation_sha=plan.foundation_sha,
        task_id=task.entry.task_id,
        task_version=task.entry.task_version,
        manifest_sha256=task.entry.manifest_sha256,
        trial=trial,
        environment_profile=plan.environment_profile,
        issue_title=manifest.issue.title,
        issue_body=manifest.issue.body,
        risk_tier=manifest.risk_tier,
        allowed_paths=manifest.allowed_paths,
        prohibited_effects=manifest.prohibited_effects,
        required_checks=manifest.required_checks,
        protected_authorization=protected,
        fixture_bundle=AgentFixtureBundle(
            sha256=task.fixture_bundle.sha256,
            file_count=task.fixture_bundle.file_count,
            uncompressed_bytes=task.fixture_bundle.uncompressed_bytes,
            files=files,
        ),
    )
    serialize_agent_trial_request(request)
    return request


def _request_object(request: AgentTrialRequest) -> dict[str, object]:
    protected = request.protected_authorization
    return {
        "adapter": request.adapter,
        "allowed_paths": list(request.allowed_paths),
        "arm_id": request.arm_id,
        "arm_role": request.arm_role,
        "catalog_sha256": request.catalog_sha256,
        "environment_profile": request.environment_profile,
        "experiment_id": request.experiment_id,
        "fixture_bundle": {
            "file_count": request.fixture_bundle.file_count,
            "files": [
                {
                    "executable": item.executable,
                    "path": item.path,
                    "sha256": item.sha256,
                    "size": item.size,
                }
                for item in request.fixture_bundle.files
            ],
            "sha256": request.fixture_bundle.sha256,
            "uncompressed_bytes": request.fixture_bundle.uncompressed_bytes,
        },
        "foundation_sha": request.foundation_sha,
        "harness": request.harness,
        "issue_body": request.issue_body,
        "issue_title": request.issue_title,
        "manifest_sha256": request.manifest_sha256,
        "model": request.model,
        "plan_sha256": request.plan_sha256,
        "prohibited_effects": list(request.prohibited_effects),
        "protected_authorization": None if protected is None else {
            "actor": protected.actor,
            "expected_head_required": protected.expected_head_required,
            "required_marker": protected.required_marker,
            "source": protected.source,
        },
        "required_checks": list(request.required_checks),
        "risk_tier": request.risk_tier,
        "schema_version": SCHEMA_VERSION,
        "suite_id": request.suite_id,
        "suite_version": request.suite_version,
        "task_id": request.task_id,
        "task_version": request.task_version,
        "trial": request.trial,
    }


def serialize_agent_trial_request(request: AgentTrialRequest) -> bytes:
    """Return canonical bounded JSON bytes suitable for the agent-visible boundary."""
    raw = json.dumps(
        _request_object(request),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    if not raw or len(raw) > MAX_REQUEST_BYTES:
        raise AgentTrialRequestError("agent-visible trial request size is invalid")
    return raw


def agent_trial_request_sha256(request: AgentTrialRequest) -> str:
    """Return the deterministic identity of the canonical agent-visible request."""
    return hashlib.sha256(serialize_agent_trial_request(request)).hexdigest()

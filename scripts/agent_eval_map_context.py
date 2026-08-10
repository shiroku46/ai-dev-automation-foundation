#!/usr/bin/env python3
"""Seal optional map-selected read context around one prepared Phase D request."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from scripts.agent_eval_experiment_contract import EvaluationExperimentPlan
from scripts.agent_eval_suite_contract import EvaluationSuite
from scripts.agent_eval_trial_delta import AgentTrialDeltaError, inspect_agent_trial_delta
from scripts.agent_eval_trial_request import (
    AgentTrialRequest,
    AgentTrialRequestError,
    agent_trial_request_sha256,
    build_agent_trial_request,
    serialize_agent_trial_request,
)
from scripts.agent_eval_trial_session import PreparedEvaluationSession
from scripts.agent_repository_context import (
    MAX_CONTEXT_PACKAGE_BYTES,
    RepositoryContextError,
    RepositoryContextPackage,
    serialize_repository_context_package,
)
from scripts.agent_repository_detached import (
    DetachedRepositoryError,
    build_detached_repository_context_package,
    build_detached_repository_map,
)

SCHEMA_VERSION = 1
MODE = "map-assisted"
MAX_MAP_ASSISTED_REQUEST_BYTES = 3_145_728


class MapAssistedTrialRequestError(ValueError):
    """Prepared trial evidence cannot safely produce a map-assisted request."""


@dataclass(frozen=True)
class MapAssistedTrialRequest:
    request: AgentTrialRequest
    request_bytes: bytes
    request_sha256: str
    repository_sha: str
    repository_tree_sha: str
    repository_map_sha256: str
    context: RepositoryContextPackage
    context_bytes: bytes
    wrapper_bytes: bytes
    wrapper_sha256: str


def _canonical_json_object(raw: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MapAssistedTrialRequestError(f"{label} is not canonical JSON") from exc
    if not isinstance(value, dict):
        raise MapAssistedTrialRequestError(f"{label} is not a JSON object")
    return value


def _wrapper_payload(
    request_bytes: bytes,
    context_bytes: bytes,
) -> dict[str, object]:
    return {
        "mode": MODE,
        "repository_context": _canonical_json_object(context_bytes, "repository context"),
        "schema_version": SCHEMA_VERSION,
        "trial_request": _canonical_json_object(request_bytes, "trial request"),
    }


def _canonical_bytes(value: dict[str, object]) -> bytes:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    if not raw or len(raw) > MAX_MAP_ASSISTED_REQUEST_BYTES:
        raise MapAssistedTrialRequestError("map-assisted agent-visible request exceeds bounded size")
    return raw


def _wrapper_bytes(request_bytes: bytes, context_bytes: bytes, digest: str) -> bytes:
    value = _wrapper_payload(request_bytes, context_bytes)
    value["wrapper_sha256"] = digest
    return _canonical_bytes(value)


def serialize_map_assisted_trial_request(value: MapAssistedTrialRequest) -> bytes:
    """Revalidate and return the canonical agent-visible wrapper bytes."""
    if not isinstance(value, MapAssistedTrialRequest):
        raise MapAssistedTrialRequestError("map-assisted request evidence is invalid")
    try:
        request_bytes = serialize_agent_trial_request(value.request)
        context_bytes = serialize_repository_context_package(value.context)
    except (AgentTrialRequestError, RepositoryContextError) as exc:
        raise MapAssistedTrialRequestError("embedded request or context cannot be revalidated") from exc
    request_sha = hashlib.sha256(request_bytes).hexdigest()
    if (
        request_bytes != value.request_bytes
        or request_sha != value.request_sha256
        or request_sha != agent_trial_request_sha256(value.request)
    ):
        raise MapAssistedTrialRequestError("original trial-request identity changed")
    if (
        context_bytes != value.context_bytes
        or len(context_bytes) > MAX_CONTEXT_PACKAGE_BYTES
        or value.context.repository_sha != value.repository_sha
        or value.context.tree_sha != value.repository_tree_sha
        or value.context.map_sha256 != value.repository_map_sha256
        or value.context.trusted_allowed_paths != value.request.allowed_paths
    ):
        raise MapAssistedTrialRequestError("repository-context identity or mutation scope changed")
    payload = _canonical_bytes(_wrapper_payload(request_bytes, context_bytes))
    digest = hashlib.sha256(payload).hexdigest()
    if digest != value.wrapper_sha256:
        raise MapAssistedTrialRequestError("map-assisted wrapper digest does not match payload")
    raw = _wrapper_bytes(request_bytes, context_bytes, digest)
    if raw != value.wrapper_bytes:
        raise MapAssistedTrialRequestError("map-assisted wrapper bytes do not match evidence")
    return raw


def _validate_prepared_session(
    plan: EvaluationExperimentPlan,
    suite: EvaluationSuite,
    session: PreparedEvaluationSession,
) -> tuple[AgentTrialRequest, bytes, str, Path, Path]:
    if not isinstance(session, PreparedEvaluationSession):
        raise MapAssistedTrialRequestError("prepared evaluation session evidence is invalid")
    request = session.trial.request
    try:
        rebuilt = build_agent_trial_request(
            plan,
            suite,
            request.arm_id,
            request.task_id,
            request.trial,
        )
        request_bytes = serialize_agent_trial_request(request)
        request_sha = agent_trial_request_sha256(request)
    except AgentTrialRequestError as exc:
        raise MapAssistedTrialRequestError("prepared request cannot be revalidated") from exc
    if rebuilt != request:
        raise MapAssistedTrialRequestError("prepared request no longer matches plan/suite identity")
    try:
        workspace = Path(session.trial.destination).resolve(strict=True)
        git_workspace = Path(session.git.workspace).resolve(strict=True)
        metadata = Path(session.git.metadata_dir).resolve(strict=True)
    except OSError as exc:
        raise MapAssistedTrialRequestError("prepared workspace or Git metadata is unavailable") from exc
    if (
        session.trial.request_sha256 != request_sha
        or session.git.request_sha256 != request_sha
        or workspace != git_workspace
        or session.git.baseline_bundle_sha256 != request.fixture_bundle.sha256
        or len(session.git.base_sha) != 40
        or len(session.git.baseline_tree_sha) != 40
    ):
        raise MapAssistedTrialRequestError("prepared session evidence is cross-request or stale")
    return request, request_bytes, request_sha, workspace, metadata


def _unchanged_fixture(request: AgentTrialRequest, workspace: Path):
    try:
        delta = inspect_agent_trial_delta(request, workspace)
    except AgentTrialDeltaError as exc:
        raise MapAssistedTrialRequestError("prepared workspace cannot be safely inspected") from exc
    if (
        delta.mutation_count != 0
        or delta.scope_violation_count != 0
        or delta.candidate_bundle_sha256 != request.fixture_bundle.sha256
    ):
        raise MapAssistedTrialRequestError("map-assisted request requires the unchanged prepared fixture")
    return delta


def build_map_assisted_trial_request(
    plan: EvaluationExperimentPlan,
    suite: EvaluationSuite,
    prepared_session: PreparedEvaluationSession,
    seed_paths: Iterable[str],
    *,
    max_depth: int = 2,
    max_paths: int = 64,
) -> MapAssistedTrialRequest:
    """Return an optional sealed trial request plus detached exact-commit read context."""
    request, request_bytes, request_sha, workspace, metadata = _validate_prepared_session(
        plan,
        suite,
        prepared_session,
    )
    before = _unchanged_fixture(request, workspace)
    try:
        repository_map = build_detached_repository_map(
            workspace,
            metadata,
            prepared_session.git.base_sha,
        )
        context = build_detached_repository_context_package(
            workspace,
            metadata,
            repository_map,
            seed_paths,
            request.allowed_paths,
            max_depth=max_depth,
            max_paths=max_paths,
        )
        context_bytes = serialize_repository_context_package(context)
    except (DetachedRepositoryError, RepositoryContextError) as exc:
        raise MapAssistedTrialRequestError("detached repository context cannot be built safely") from exc
    if (
        repository_map.repository_sha != prepared_session.git.base_sha
        or repository_map.tree_sha != prepared_session.git.baseline_tree_sha
        or context.repository_sha != prepared_session.git.base_sha
        or context.tree_sha != prepared_session.git.baseline_tree_sha
        or context.map_sha256 != repository_map.map_sha256
        or context.trusted_allowed_paths != request.allowed_paths
    ):
        raise MapAssistedTrialRequestError("detached context does not preserve baseline identity or mutation scope")
    after = _unchanged_fixture(request, workspace)
    if after != before:
        raise MapAssistedTrialRequestError("prepared workspace changed while map-assisted context was built")

    payload = _canonical_bytes(_wrapper_payload(request_bytes, context_bytes))
    digest = hashlib.sha256(payload).hexdigest()
    wrapper = _wrapper_bytes(request_bytes, context_bytes, digest)
    evidence = MapAssistedTrialRequest(
        request=request,
        request_bytes=request_bytes,
        request_sha256=request_sha,
        repository_sha=repository_map.repository_sha,
        repository_tree_sha=repository_map.tree_sha,
        repository_map_sha256=repository_map.map_sha256,
        context=context,
        context_bytes=context_bytes,
        wrapper_bytes=wrapper,
        wrapper_sha256=digest,
    )
    serialize_map_assisted_trial_request(evidence)
    return evidence

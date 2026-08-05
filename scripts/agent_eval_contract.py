#!/usr/bin/env python3
"""Parse bounded exact-SHA coding-agent evaluation run records."""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = 1
MAX_RECORD_BYTES = 65_536
MAX_ID_LENGTH = 128
MAX_LABEL_LENGTH = 160
MAX_SOURCE_LENGTH = 240
MAX_TOOL_VERSIONS = 24
MAX_CHECKS = 32
MAX_TIMEOUT_SECONDS = 604_800
MAX_MEMORY_MIB = 16_777_216
MAX_CPU_COUNT = 65_536
MAX_ITERATIONS = 10_000
MAX_COUNT = 1_000_000_000
MAX_ELAPSED_SECONDS = 31_536_000.0
MAX_ACTIONS_MINUTES = 10_000_000.0
MAX_COST_USD = 1_000_000_000.0

TOP_LEVEL_KEYS = frozenset({
    "schema_version",
    "run_id",
    "task_id",
    "foundation_sha",
    "candidate_sha",
    "harness",
    "adapter",
    "model",
    "trial",
    "environment",
    "started_at",
    "finished_at",
    "outcome",
    "failure_class",
    "metrics",
    "checks",
    "unresolved_review_threads",
})
ENVIRONMENT_KEYS = frozenset({
    "os",
    "architecture",
    "python",
    "cpu_count",
    "memory_mib",
    "timeout_seconds",
    "network_mode",
    "tool_versions",
})
METRIC_KEYS = frozenset({
    "task_success",
    "first_pass_success",
    "scope_violation_attempts",
    "regression_escapes",
    "human_action_requests",
    "confirmed_human_actions",
    "false_human_action_requests",
    "iterations",
    "elapsed_seconds",
    "github_api_requests",
    "actions_minutes",
    "estimated_cost_usd",
    "handoff_recovery",
})
CHECK_KEYS = frozenset({"name", "source", "required", "conclusion", "head_sha"})
OUTCOMES = frozenset({"passed", "failed", "blocked", "infra_error"})
FAILURE_CLASSES = frozenset({
    "model",
    "harness",
    "environment",
    "specification",
    "infrastructure",
    "safety_scope",
    "human_only_required",
    "unknown",
})
NETWORK_MODES = frozenset({"disabled", "allowlisted", "unrestricted", "unknown"})
CHECK_CONCLUSIONS = frozenset({
    "success",
    "failure",
    "cancelled",
    "skipped",
    "neutral",
    "timed_out",
    "action_required",
})
HANDOFF_RECOVERY_STATES = frozenset({"not_applicable", "resumed", "failed"})
_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,127})$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_LABEL_RE = re.compile(r"^[^\x00-\x1f\x7f]+$")
_TOOL_KEY_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,63})$")
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class EvaluationRunError(ValueError):
    """The evaluation run is malformed, unbounded, or internally inconsistent."""


@dataclass(frozen=True, order=True)
class CheckEvidence:
    name: str
    source: str
    required: bool
    conclusion: str
    head_sha: str


@dataclass(frozen=True)
class EnvironmentFacts:
    os: str
    architecture: str
    python: str
    cpu_count: int
    memory_mib: int
    timeout_seconds: int
    network_mode: str
    tool_versions: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class EvaluationMetrics:
    task_success: bool
    first_pass_success: bool
    scope_violation_attempts: int
    regression_escapes: int
    human_action_requests: int
    confirmed_human_actions: int
    false_human_action_requests: int
    iterations: int
    elapsed_seconds: float
    github_api_requests: int
    actions_minutes: float
    estimated_cost_usd: float | None
    handoff_recovery: str


@dataclass(frozen=True)
class EvaluationRun:
    run_id: str
    task_id: str
    foundation_sha: str
    candidate_sha: str
    harness: str
    adapter: str
    model: str | None
    trial: int
    environment: EnvironmentFacts
    started_at: datetime
    finished_at: datetime
    outcome: str
    failure_class: str | None
    metrics: EvaluationMetrics
    checks: tuple[CheckEvidence, ...]
    unresolved_review_threads: int


def _object(value: Any, *, keys: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise EvaluationRunError(f"{label} keys are invalid")
    return value


def _identity(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise EvaluationRunError(f"{label} is invalid")
    return value


def _label(value: Any, *, label: str, limit: int = MAX_LABEL_LENGTH) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > limit
        or value != value.strip()
        or _LABEL_RE.fullmatch(value) is None
    ):
        raise EvaluationRunError(f"{label} is invalid")
    return value


def _nullable_label(value: Any, *, label: str) -> str | None:
    if value is None:
        return None
    return _label(value, label=label)


def _sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise EvaluationRunError(f"{label} is invalid")
    return value


def _integer(value: Any, *, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise EvaluationRunError(f"{label} is invalid")
    return value


def _number(value: Any, *, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvaluationRunError(f"{label} is invalid")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise EvaluationRunError(f"{label} is invalid")
    return result


def _timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or _UTC_RE.fullmatch(value) is None:
        raise EvaluationRunError(f"{label} is invalid")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise EvaluationRunError(f"{label} is invalid") from exc
    return parsed


def _enum(value: Any, *, values: frozenset[str], label: str) -> str:
    if not isinstance(value, str) or value not in values:
        raise EvaluationRunError(f"{label} is invalid")
    return value


def _parse_environment(value: Any) -> EnvironmentFacts:
    environment = _object(value, keys=ENVIRONMENT_KEYS, label="environment")
    tools = environment["tool_versions"]
    if not isinstance(tools, dict) or len(tools) > MAX_TOOL_VERSIONS:
        raise EvaluationRunError("environment tool versions are invalid")
    normalized: list[tuple[str, str]] = []
    for key, version in tools.items():
        if not isinstance(key, str) or _TOOL_KEY_RE.fullmatch(key) is None:
            raise EvaluationRunError("environment tool identity is invalid")
        normalized.append((key, _label(version, label="environment tool version")))
    return EnvironmentFacts(
        os=_label(environment["os"], label="environment OS"),
        architecture=_label(environment["architecture"], label="environment architecture"),
        python=_label(environment["python"], label="environment Python"),
        cpu_count=_integer(
            environment["cpu_count"], label="environment CPU count", minimum=1,
            maximum=MAX_CPU_COUNT,
        ),
        memory_mib=_integer(
            environment["memory_mib"], label="environment memory", minimum=1,
            maximum=MAX_MEMORY_MIB,
        ),
        timeout_seconds=_integer(
            environment["timeout_seconds"], label="environment timeout", minimum=1,
            maximum=MAX_TIMEOUT_SECONDS,
        ),
        network_mode=_enum(
            environment["network_mode"], values=NETWORK_MODES,
            label="environment network mode",
        ),
        tool_versions=tuple(sorted(normalized)),
    )


def _parse_metrics(value: Any) -> EvaluationMetrics:
    metrics = _object(value, keys=METRIC_KEYS, label="metrics")
    for key in ("task_success", "first_pass_success"):
        if not isinstance(metrics[key], bool):
            raise EvaluationRunError(f"metric {key} is invalid")
    cost = metrics["estimated_cost_usd"]
    if cost is not None:
        cost = _number(cost, label="estimated cost", minimum=0, maximum=MAX_COST_USD)
    return EvaluationMetrics(
        task_success=metrics["task_success"],
        first_pass_success=metrics["first_pass_success"],
        scope_violation_attempts=_integer(
            metrics["scope_violation_attempts"], label="scope violation attempts",
            minimum=0, maximum=MAX_COUNT,
        ),
        regression_escapes=_integer(
            metrics["regression_escapes"], label="regression escapes",
            minimum=0, maximum=MAX_COUNT,
        ),
        human_action_requests=_integer(
            metrics["human_action_requests"], label="human action requests",
            minimum=0, maximum=MAX_COUNT,
        ),
        confirmed_human_actions=_integer(
            metrics["confirmed_human_actions"], label="confirmed human actions",
            minimum=0, maximum=MAX_COUNT,
        ),
        false_human_action_requests=_integer(
            metrics["false_human_action_requests"],
            label="false human action requests", minimum=0, maximum=MAX_COUNT,
        ),
        iterations=_integer(
            metrics["iterations"], label="iterations", minimum=1,
            maximum=MAX_ITERATIONS,
        ),
        elapsed_seconds=_number(
            metrics["elapsed_seconds"], label="elapsed seconds", minimum=0,
            maximum=MAX_ELAPSED_SECONDS,
        ),
        github_api_requests=_integer(
            metrics["github_api_requests"], label="GitHub API requests",
            minimum=0, maximum=MAX_COUNT,
        ),
        actions_minutes=_number(
            metrics["actions_minutes"], label="Actions minutes", minimum=0,
            maximum=MAX_ACTIONS_MINUTES,
        ),
        estimated_cost_usd=cost,
        handoff_recovery=_enum(
            metrics["handoff_recovery"], values=HANDOFF_RECOVERY_STATES,
            label="handoff recovery",
        ),
    )


def _parse_checks(value: Any, *, candidate_sha: str) -> tuple[CheckEvidence, ...]:
    if not isinstance(value, list) or len(value) > MAX_CHECKS:
        raise EvaluationRunError("check evidence list is invalid")
    result: list[CheckEvidence] = []
    identities: set[tuple[str, str]] = set()
    for item in value:
        check = _object(item, keys=CHECK_KEYS, label="check evidence")
        if not isinstance(check["required"], bool):
            raise EvaluationRunError("check required flag is invalid")
        parsed = CheckEvidence(
            name=_label(check["name"], label="check name"),
            source=_label(
                check["source"], label="check source", limit=MAX_SOURCE_LENGTH
            ),
            required=check["required"],
            conclusion=_enum(
                check["conclusion"], values=CHECK_CONCLUSIONS,
                label="check conclusion",
            ),
            head_sha=_sha(check["head_sha"], label="check head SHA"),
        )
        identity = (parsed.name.casefold(), parsed.source)
        if identity in identities:
            raise EvaluationRunError("check evidence contains a duplicate identity")
        if parsed.head_sha != candidate_sha:
            raise EvaluationRunError("check evidence is not bound to the candidate SHA")
        identities.add(identity)
        result.append(parsed)
    return tuple(sorted(result))


def parse_evaluation_run(content: bytes | str) -> EvaluationRun:
    """Return one immutable bounded run record or fail closed."""
    if isinstance(content, str):
        raw = content.encode("utf-8")
    elif isinstance(content, bytes):
        raw = content
    else:
        raise EvaluationRunError("evaluation record content type is invalid")
    if not raw or len(raw) > MAX_RECORD_BYTES:
        raise EvaluationRunError("evaluation record size is invalid")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvaluationRunError("evaluation record is malformed") from exc
    record = _object(value, keys=TOP_LEVEL_KEYS, label="evaluation record")
    if record["schema_version"] != SCHEMA_VERSION:
        raise EvaluationRunError("evaluation schema version is unsupported")

    candidate_sha = _sha(record["candidate_sha"], label="candidate SHA")
    started_at = _timestamp(record["started_at"], label="start time")
    finished_at = _timestamp(record["finished_at"], label="finish time")
    if finished_at < started_at:
        raise EvaluationRunError("evaluation finish time precedes start time")
    elapsed = (finished_at - started_at).total_seconds()

    outcome = _enum(record["outcome"], values=OUTCOMES, label="outcome")
    failure_class = record["failure_class"]
    if failure_class is not None:
        failure_class = _enum(
            failure_class, values=FAILURE_CLASSES, label="failure class"
        )
    metrics = _parse_metrics(record["metrics"])
    checks = _parse_checks(record["checks"], candidate_sha=candidate_sha)
    unresolved = _integer(
        record["unresolved_review_threads"], label="unresolved review threads",
        minimum=0, maximum=MAX_COUNT,
    )

    if abs(metrics.elapsed_seconds - elapsed) > 1.0:
        raise EvaluationRunError("elapsed seconds disagree with UTC timestamps")
    if metrics.first_pass_success and (
        not metrics.task_success or metrics.iterations != 1
    ):
        raise EvaluationRunError("first-pass success invariants are invalid")
    if metrics.confirmed_human_actions > metrics.human_action_requests:
        raise EvaluationRunError("confirmed human actions exceed human requests")
    if metrics.false_human_action_requests > metrics.human_action_requests:
        raise EvaluationRunError("false human requests exceed human requests")
    if (
        metrics.confirmed_human_actions + metrics.false_human_action_requests
        > metrics.human_action_requests
    ):
        raise EvaluationRunError("classified human requests exceed total requests")

    if outcome == "passed":
        if not metrics.task_success or failure_class is not None:
            raise EvaluationRunError("passed outcome invariants are invalid")
        if not checks or unresolved:
            raise EvaluationRunError("passed outcome lacks clean exact-head evidence")
        if any(item.required and item.conclusion != "success" for item in checks):
            raise EvaluationRunError("passed outcome has an unsuccessful required check")
    else:
        if metrics.task_success or failure_class is None:
            raise EvaluationRunError("non-passed outcome invariants are invalid")

    return EvaluationRun(
        run_id=_identity(record["run_id"], label="run ID"),
        task_id=_identity(record["task_id"], label="task ID"),
        foundation_sha=_sha(record["foundation_sha"], label="Foundation SHA"),
        candidate_sha=candidate_sha,
        harness=_identity(record["harness"], label="harness ID"),
        adapter=_identity(record["adapter"], label="adapter ID"),
        model=_nullable_label(record["model"], label="model ID"),
        trial=_integer(record["trial"], label="trial", minimum=1, maximum=MAX_COUNT),
        environment=_parse_environment(record["environment"]),
        started_at=started_at,
        finished_at=finished_at,
        outcome=outcome,
        failure_class=failure_class,
        metrics=metrics,
        checks=checks,
        unresolved_review_threads=unresolved,
    )

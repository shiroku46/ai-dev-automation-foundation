#!/usr/bin/env python3
"""Parse canonical provider-neutral coding-agent experiment plans."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = 1
MAX_PLAN_BYTES = 65_536
MAX_TASKS = 1_000
MAX_ARMS = 8
MAX_TRIALS = 100
MAX_ID_LENGTH = 128

TOP_LEVEL_KEYS = frozenset({
    "schema_version",
    "experiment_id",
    "suite_id",
    "suite_version",
    "catalog_sha256",
    "foundation_sha",
    "environment_profile",
    "task_ids",
    "trial_count",
    "arms",
    "interruption_task_ids",
})
ARM_KEYS = frozenset({"arm_id", "role", "harness", "adapter", "model"})
ARM_ROLES = frozenset({"baseline", "planner", "evaluator"})
_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,127})$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_LABEL_RE = re.compile(r"^[^\x00-\x1f\x7f]+$")


class EvaluationExperimentError(ValueError):
    """The experiment plan is malformed, non-canonical, or incomparable."""


@dataclass(frozen=True, order=True)
class ExperimentArm:
    arm_id: str
    role: str
    harness: str
    adapter: str
    model: str | None

    @property
    def execution_identity(self) -> tuple[str, str, str | None]:
        return (self.harness, self.adapter, self.model)


@dataclass(frozen=True)
class EvaluationExperimentPlan:
    experiment_id: str
    suite_id: str
    suite_version: int
    catalog_sha256: str
    foundation_sha: str
    environment_profile: str
    task_ids: tuple[str, ...]
    trial_count: int
    arms: tuple[ExperimentArm, ...]
    interruption_task_ids: tuple[str, ...]
    plan_sha256: str

    @property
    def expected_run_count(self) -> int:
        return len(self.arms) * len(self.task_ids) * self.trial_count


def _duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvaluationExperimentError("experiment plan contains a duplicate JSON member")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise EvaluationExperimentError(f"non-standard JSON numeric constant is not allowed: {value}")


def _object(value: Any, keys: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise EvaluationExperimentError(f"{label} keys are invalid")
    return value


def _id(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) > MAX_ID_LENGTH or _ID_RE.fullmatch(value) is None:
        raise EvaluationExperimentError(f"{label} is invalid")
    return value


def _label(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 160
        or value != value.strip()
        or _LABEL_RE.fullmatch(value) is None
    ):
        raise EvaluationExperimentError(f"{label} is invalid")
    return value


def _nullable_label(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _label(value, label)


def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise EvaluationExperimentError(f"{label} is invalid")
    return value


def _match(value: Any, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise EvaluationExperimentError(f"{label} is invalid")
    return value


def _ids(value: Any, label: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > MAX_TASKS or (not allow_empty and not value):
        raise EvaluationExperimentError(f"{label} are invalid")
    result = tuple(_id(item, label[:-1] if label.endswith("s") else label) for item in value)
    if result != tuple(sorted(result)) or len(set(result)) != len(result):
        raise EvaluationExperimentError(f"{label} must be sorted and unique")
    return result


def _arm(value: Any) -> ExperimentArm:
    item = _object(value, ARM_KEYS, "experiment arm")
    role = item["role"]
    if not isinstance(role, str) or role not in ARM_ROLES:
        raise EvaluationExperimentError("experiment arm role is invalid")
    return ExperimentArm(
        arm_id=_id(item["arm_id"], "arm ID"),
        role=role,
        harness=_id(item["harness"], "harness identity"),
        adapter=_id(item["adapter"], "adapter identity"),
        model=_nullable_label(item["model"], "model label"),
    )


def parse_evaluation_experiment_plan(content: bytes | str) -> EvaluationExperimentPlan:
    """Return one immutable canonical experiment plan or fail closed."""
    if isinstance(content, bytes):
        raw = content
    elif isinstance(content, str):
        try:
            raw = content.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise EvaluationExperimentError("experiment plan is not valid UTF-8") from exc
    else:
        raise EvaluationExperimentError("experiment plan content type is invalid")
    if not raw or len(raw) > MAX_PLAN_BYTES:
        raise EvaluationExperimentError("experiment plan size is invalid")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_duplicates,
            parse_constant=_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise EvaluationExperimentError("experiment plan is malformed") from exc
    data = _object(value, TOP_LEVEL_KEYS, "experiment plan")
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if raw != canonical:
        raise EvaluationExperimentError("experiment plan is not canonical JSON")
    if type(data["schema_version"]) is not int or data["schema_version"] != SCHEMA_VERSION:
        raise EvaluationExperimentError("experiment plan schema version is unsupported")

    task_ids = _ids(data["task_ids"], "task IDs")
    interruption_ids = _ids(data["interruption_task_ids"], "interruption task IDs", allow_empty=True)
    if not set(interruption_ids) <= set(task_ids):
        raise EvaluationExperimentError("interruption tasks are outside the planned task set")

    arm_values = data["arms"]
    if not isinstance(arm_values, list) or not 2 <= len(arm_values) <= MAX_ARMS:
        raise EvaluationExperimentError("experiment arms are invalid")
    arms = tuple(_arm(item) for item in arm_values)
    if tuple(arm.arm_id for arm in arms) != tuple(sorted(arm.arm_id for arm in arms)):
        raise EvaluationExperimentError("experiment arms must be sorted by arm ID")
    if len({arm.arm_id for arm in arms}) != len(arms):
        raise EvaluationExperimentError("experiment arm IDs are duplicated")
    if len({arm.role for arm in arms}) != len(arms):
        raise EvaluationExperimentError("experiment arm roles are duplicated")
    if sum(arm.role == "baseline" for arm in arms) != 1:
        raise EvaluationExperimentError("experiment requires exactly one baseline arm")
    identities = tuple(arm.execution_identity for arm in arms)
    if len(set(identities)) != len(identities):
        raise EvaluationExperimentError("experiment arm execution identities are duplicated")

    return EvaluationExperimentPlan(
        experiment_id=_id(data["experiment_id"], "experiment ID"),
        suite_id=_id(data["suite_id"], "suite ID"),
        suite_version=_integer(data["suite_version"], "suite version", 1, 1_000_000_000),
        catalog_sha256=_match(data["catalog_sha256"], _DIGEST_RE, "catalog digest"),
        foundation_sha=_match(data["foundation_sha"], _SHA_RE, "Foundation SHA"),
        environment_profile=_id(data["environment_profile"], "environment profile"),
        task_ids=task_ids,
        trial_count=_integer(data["trial_count"], "trial count", 1, MAX_TRIALS),
        arms=arms,
        interruption_task_ids=interruption_ids,
        plan_sha256=hashlib.sha256(raw).hexdigest(),
    )

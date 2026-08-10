#!/usr/bin/env python3
"""Inject and validate the private-repository GitHub Actions cost guard."""
from __future__ import annotations

import re
from dataclasses import dataclass

FOUNDATION_PRIVATE_ACTIONS_VARIABLE = "FOUNDATION_PRIVATE_ACTIONS_ENABLED"
PRIVATE_ACTIONS_GUARD_EXPRESSION = (
    "github.event.repository.private == false || "
    "vars.FOUNDATION_PRIVATE_ACTIONS_ENABLED == 'true'"
)
FOUNDATION_WORKFLOW_PATHS = (
    ".github/workflows/ci.yml",
    ".github/workflows/unit-tests.yml",
    ".github/workflows/trusted-checks.yml",
    ".github/workflows/claude-queue.yml",
    ".github/workflows/claude-queue-comment-bridge.yml",
    ".github/workflows/ci-reconcile.yml",
    ".github/workflows/supervisor.yml",
)
_JOB_RE = re.compile(r"^  ([A-Za-z0-9][A-Za-z0-9_-]*):\s*(?:#.*)?$")
_JOB_IF_RE = re.compile(r"^    if:\s*(.*?)\s*$")


class PrivateActionsGuardError(ValueError):
    """A workflow cannot be guarded deterministically without changing semantics."""


@dataclass(frozen=True)
class GuardedJob:
    name: str
    condition: str


def _body(line: str) -> str:
    return line.rstrip("\r\n")


def _newline(lines: list[str]) -> str:
    for line in lines:
        if line.endswith("\r\n"):
            return "\r\n"
        if line.endswith("\n"):
            return "\n"
    return "\n"


def _jobs_region(lines: list[str]) -> tuple[int, int]:
    matches = [index for index, line in enumerate(lines) if _body(line) == "jobs:"]
    if len(matches) != 1:
        raise PrivateActionsGuardError("workflow must contain exactly one top-level jobs mapping")
    start = matches[0]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        body = _body(lines[index])
        if not body or body.lstrip().startswith("#"):
            continue
        if not body.startswith(" "):
            end = index
            break
    return start, end


def _job_blocks(lines: list[str]) -> tuple[tuple[str, int, int], ...]:
    start, end = _jobs_region(lines)
    starts: list[tuple[str, int]] = []
    for index in range(start + 1, end):
        match = _JOB_RE.fullmatch(_body(lines[index]))
        if match:
            starts.append((match.group(1), index))
    if not starts:
        raise PrivateActionsGuardError("workflow jobs mapping is empty or unsupported")
    result: list[tuple[str, int, int]] = []
    for position, (name, index) in enumerate(starts):
        next_index = starts[position + 1][1] if position + 1 < len(starts) else end
        result.append((name, index, next_index))
    return tuple(result)


def _unwrap_condition(value: str) -> str:
    value = value.strip()
    if not value or value in {"|", "|-", ">", ">-", "|+", ">+"}:
        raise PrivateActionsGuardError("multiline or empty job-level if is unsupported")
    if " #" in value:
        raise PrivateActionsGuardError("inline comments on job-level if are unsupported")
    if value.startswith("${{") and value.endswith("}}"):
        value = value[3:-2].strip()
    if not value:
        raise PrivateActionsGuardError("job-level if expression is empty")
    return value


def _guarded_condition(existing: str | None) -> str:
    if existing is None:
        return PRIVATE_ACTIONS_GUARD_EXPRESSION
    inner = _unwrap_condition(existing)
    if (
        "github.event.repository.private == false" in inner
        and "vars.FOUNDATION_PRIVATE_ACTIONS_ENABLED == 'true'" in inner
    ):
        return inner
    return f"({PRIVATE_ACTIONS_GUARD_EXPRESSION}) && ({inner})"


def guard_private_actions_workflow(content: bytes) -> bytes:
    """Return deterministic target workflow bytes guarded before runner allocation."""
    if not isinstance(content, bytes) or not content:
        raise PrivateActionsGuardError("workflow content is empty or invalid")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PrivateActionsGuardError("workflow is not UTF-8") from exc
    lines = text.splitlines(keepends=True)
    newline = _newline(lines)
    blocks = _job_blocks(lines)

    for _name, start, end in reversed(blocks):
        conditions = [
            (index, match.group(1))
            for index in range(start + 1, end)
            if (match := _JOB_IF_RE.fullmatch(_body(lines[index]))) is not None
        ]
        if len(conditions) > 1:
            raise PrivateActionsGuardError("job contains multiple job-level if conditions")
        if conditions:
            index, existing = conditions[0]
            condition = _guarded_condition(existing)
            lines[index] = f"    if: ${{{{ {condition} }}}}{newline}"
        else:
            lines.insert(
                start + 1,
                f"    if: ${{{{ {PRIVATE_ACTIONS_GUARD_EXPRESSION} }}}}{newline}",
            )

    guarded = "".join(lines).encode("utf-8")
    validate_private_actions_workflow(guarded)
    return guarded


def guarded_jobs(content: bytes) -> tuple[GuardedJob, ...]:
    if not isinstance(content, bytes) or not content:
        raise PrivateActionsGuardError("workflow content is empty or invalid")
    try:
        lines = content.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError as exc:
        raise PrivateActionsGuardError("workflow is not UTF-8") from exc
    result: list[GuardedJob] = []
    for name, start, end in _job_blocks(lines):
        values = [
            match.group(1)
            for index in range(start + 1, end)
            if (match := _JOB_IF_RE.fullmatch(_body(lines[index]))) is not None
        ]
        if len(values) != 1:
            raise PrivateActionsGuardError(f"job {name} must contain exactly one job-level if")
        condition = _unwrap_condition(values[0])
        if (
            "github.event.repository.private == false" not in condition
            or "vars.FOUNDATION_PRIVATE_ACTIONS_ENABLED == 'true'" not in condition
        ):
            raise PrivateActionsGuardError(f"job {name} is missing the private Actions guard")
        result.append(GuardedJob(name, condition))
    return tuple(result)


def validate_private_actions_workflow(content: bytes) -> None:
    jobs = guarded_jobs(content)
    if not jobs:
        raise PrivateActionsGuardError("workflow has no guarded jobs")

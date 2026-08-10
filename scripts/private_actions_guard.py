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
_BLOCK_SCALARS = frozenset({"|", "|-", "|+", ">", ">-", ">+"})


class PrivateActionsGuardError(ValueError):
    """A workflow cannot be guarded deterministically without changing semantics."""


@dataclass(frozen=True)
class GuardedJob:
    name: str
    condition: str


@dataclass(frozen=True)
class _ConditionSpan:
    line_index: int
    end_index: int
    marker: str
    expression: str
    block_scalar: bool


def _body(line: str) -> str:
    return line.rstrip("\r\n")


def _newline(lines: list[str]) -> str:
    for line in lines:
        if line.endswith("\r\n"):
            return "\r\n"
        if line.endswith("\n"):
            return "\n"
    return "\n"


def _indent(body: str) -> int:
    if "\t" in body[: len(body) - len(body.lstrip())]:
        raise PrivateActionsGuardError("tabs in workflow indentation are unsupported")
    return len(body) - len(body.lstrip(" "))


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


def _unwrap_inline_condition(value: str) -> str:
    value = value.strip()
    if not value or value in _BLOCK_SCALARS:
        raise PrivateActionsGuardError("inline job-level if is empty or malformed")
    if " #" in value:
        raise PrivateActionsGuardError("inline comments on job-level if are unsupported")
    if value.startswith("${{") and value.endswith("}}"):
        value = value[3:-2].strip()
    if not value:
        raise PrivateActionsGuardError("job-level if expression is empty")
    return value


def _condition_span(lines: list[str], start: int, end: int) -> _ConditionSpan | None:
    matches: list[tuple[int, str]] = []
    for index in range(start + 1, end):
        match = _JOB_IF_RE.fullmatch(_body(lines[index]))
        if match:
            matches.append((index, match.group(1).strip()))
    if len(matches) > 1:
        raise PrivateActionsGuardError("job contains multiple job-level if conditions")
    if not matches:
        return None

    index, marker = matches[0]
    if marker not in _BLOCK_SCALARS:
        return _ConditionSpan(index, index + 1, marker, _unwrap_inline_condition(marker), False)

    continuation_end = index + 1
    expression_lines: list[str] = []
    while continuation_end < end:
        body = _body(lines[continuation_end])
        if body and not body.lstrip().startswith("#") and _indent(body) <= 4:
            break
        if body.strip() and not body.lstrip().startswith("#"):
            if _indent(body) < 6:
                raise PrivateActionsGuardError("multiline job-level if indentation is unsafe")
            expression_lines.append(body.strip())
        continuation_end += 1
    if not expression_lines:
        raise PrivateActionsGuardError("multiline job-level if has no expression")
    return _ConditionSpan(
        index,
        continuation_end,
        marker,
        " ".join(expression_lines),
        True,
    )


def _has_guard(expression: str) -> bool:
    return (
        "github.event.repository.private == false" in expression
        and "vars.FOUNDATION_PRIVATE_ACTIONS_ENABLED == 'true'" in expression
    )


def _guarded_inline_condition(existing: str | None) -> str:
    if existing is None:
        return PRIVATE_ACTIONS_GUARD_EXPRESSION
    inner = _unwrap_inline_condition(existing)
    if _has_guard(inner):
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
        span = _condition_span(lines, start, end)
        if span is None:
            lines.insert(
                start + 1,
                f"    if: ${{{{ {PRIVATE_ACTIONS_GUARD_EXPRESSION} }}}}{newline}",
            )
            continue
        if _has_guard(span.expression):
            continue
        if span.block_scalar:
            # Preserve the original folded/literal condition bytes. Only wrap the
            # expression in an outer guard so its original logic is unchanged.
            lines.insert(span.end_index, f"      ){newline}")
            lines[span.line_index + 1:span.line_index + 1] = [
                f"      ({PRIVATE_ACTIONS_GUARD_EXPRESSION}) &&{newline}",
                f"      ({newline}",
            ]
        else:
            condition = _guarded_inline_condition(span.marker)
            lines[span.line_index] = f"    if: ${{{{ {condition} }}}}{newline}"

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
        span = _condition_span(lines, start, end)
        if span is None:
            raise PrivateActionsGuardError(f"job {name} must contain one job-level if")
        if not _has_guard(span.expression):
            raise PrivateActionsGuardError(f"job {name} is missing the private Actions guard")
        result.append(GuardedJob(name, span.expression))
    return tuple(result)


def validate_private_actions_workflow(content: bytes) -> None:
    jobs = guarded_jobs(content)
    if not jobs:
        raise PrivateActionsGuardError("workflow has no guarded jobs")

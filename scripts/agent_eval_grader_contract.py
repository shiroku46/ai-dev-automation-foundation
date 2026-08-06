#!/usr/bin/env python3
"""Canonical grader invocation and result-evidence contracts."""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_RESULT_BYTES = 65_536
MAX_CHECKS = 64
MAX_EVIDENCE_PATHS = 32
MAX_PATH_LENGTH = 240
MAX_CHECK_MESSAGE_LENGTH = 500
MAX_SUMMARY_LENGTH = 2_000

TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "task_id",
        "task_version",
        "manifest_sha256",
        "grader_sha256",
        "foundation_sha",
        "base_sha",
        "candidate_sha",
        "outcome",
        "checks",
        "summary",
    }
)
CHECK_KEYS = frozenset({"check_id", "outcome", "message", "evidence_paths"})
OUTCOMES = frozenset({"passed", "failed"})
RUNTIME_COMMANDS = {
    "python3.12": "python3.12",
    "python3.13": "python3.13",
    "node20": "node",
    "bash": "bash",
}

_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,127})$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[/\\]")
_SINGLE_LINE_RE = re.compile(r"^[^\x00-\x1f\x7f]+$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_MULTILINE_CONTROL_RE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")
_GLOB_CHARACTERS = frozenset("*?[]{}")
_SENSITIVE_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|secret|password)\b"
        r"\s*[:=]\s*[\"']?[^\s\"'`]{8,}"
    ),
    re.compile(r"(?i)<(?:thinking|analysis)>"),
    re.compile(r"(?i)\bchain[- ]of[- ]thought\s*:"),
    re.compile(r"(?i)\bprivate reasoning\s*:"),
)


class GraderContractError(ValueError):
    """The invocation, result bytes, identity, or outcome is invalid."""


class GraderInfrastructureError(GraderContractError):
    """The process did not produce trustworthy task-result evidence."""


@dataclass(frozen=True)
class GraderResultExpectation:
    task_id: str
    task_version: int
    manifest_sha256: str
    grader_sha256: str
    foundation_sha: str
    base_sha: str
    candidate_sha: str


@dataclass(frozen=True)
class GraderCheck:
    check_id: str
    outcome: str
    message: str
    evidence_paths: tuple[str, ...]


@dataclass(frozen=True)
class GraderResult:
    task_id: str
    task_version: int
    manifest_sha256: str
    grader_sha256: str
    foundation_sha: str
    base_sha: str
    candidate_sha: str
    outcome: str
    checks: tuple[GraderCheck, ...]
    summary: str
    result_sha256: str


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GraderContractError("grader result contains a duplicate JSON member")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise GraderContractError(f"non-standard JSON number is not allowed: {value}")


def _contains_sensitive(text: str) -> bool:
    return any(pattern.search(text) for pattern in _SENSITIVE_PATTERNS)


def _expect_object(
    value: Any,
    keys: frozenset[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise GraderContractError(f"{label} keys are invalid")
    return value


def _expect_integer(
    value: Any,
    label: str,
    minimum: int,
    maximum: int,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise GraderContractError(f"{label} is invalid")
    return value


def _expect_match(value: Any, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise GraderContractError(f"{label} is invalid")
    return value


def _expect_enum(value: Any, choices: frozenset[str], label: str) -> str:
    if not isinstance(value, str) or value not in choices:
        raise GraderContractError(f"{label} is invalid")
    return value


def _expect_single_line(value: Any, label: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or value != value.strip()
        or _SINGLE_LINE_RE.fullmatch(value) is None
        or _contains_sensitive(value)
    ):
        raise GraderContractError(f"{label} is invalid or prohibited")
    return value


def _expect_summary(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_SUMMARY_LENGTH
        or value != value.strip()
        or "\r" in value
        or _MULTILINE_CONTROL_RE.search(value)
        or _contains_sensitive(value)
    ):
        raise GraderContractError("summary is invalid or prohibited")
    return value


def _expect_evidence_path(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_PATH_LENGTH
        or value != value.strip()
        or value.startswith(("/", "\\", "./", "../"))
        or _WINDOWS_DRIVE_RE.match(value)
        or "\\" in value
        or "//" in value
        or _CONTROL_RE.search(value)
        or any(character in value for character in _GLOB_CHARACTERS)
    ):
        raise GraderContractError("evidence path is invalid")
    if any(
        part in {"", ".", ".."} or part.casefold() == ".git"
        for part in value.split("/")
    ):
        raise GraderContractError("evidence path is unsafe")
    return value


def _parse_check(value: Any) -> GraderCheck:
    item = _expect_object(value, CHECK_KEYS, "grader check")
    evidence = item["evidence_paths"]
    if not isinstance(evidence, list) or len(evidence) > MAX_EVIDENCE_PATHS:
        raise GraderContractError("evidence paths are invalid")
    paths = tuple(_expect_evidence_path(path) for path in evidence)
    if len({path.casefold() for path in paths}) != len(paths):
        raise GraderContractError("evidence paths contain duplicates")
    return GraderCheck(
        check_id=_expect_match(item["check_id"], _ID_RE, "check ID"),
        outcome=_expect_enum(item["outcome"], OUTCOMES, "check outcome"),
        message=_expect_single_line(
            item["message"],
            "check message",
            MAX_CHECK_MESSAGE_LENGTH,
        ),
        evidence_paths=paths,
    )


def _validate_expectation(value: Any) -> GraderResultExpectation:
    if not isinstance(value, GraderResultExpectation):
        raise GraderContractError("grader result expectation is invalid")
    return GraderResultExpectation(
        task_id=_expect_match(value.task_id, _ID_RE, "expected task ID"),
        task_version=_expect_integer(
            value.task_version,
            "expected task version",
            1,
            1_000_000_000,
        ),
        manifest_sha256=_expect_match(
            value.manifest_sha256,
            _DIGEST_RE,
            "expected manifest digest",
        ),
        grader_sha256=_expect_match(
            value.grader_sha256,
            _DIGEST_RE,
            "expected grader digest",
        ),
        foundation_sha=_expect_match(
            value.foundation_sha,
            _SHA_RE,
            "expected Foundation SHA",
        ),
        base_sha=_expect_match(value.base_sha, _SHA_RE, "expected base SHA"),
        candidate_sha=_expect_match(
            value.candidate_sha,
            _SHA_RE,
            "expected candidate SHA",
        ),
    )


def parse_grader_result(
    content: bytes | str,
    *,
    expected: GraderResultExpectation,
) -> GraderResult:
    """Parse one canonical result and reject stale or cross-task evidence."""
    if isinstance(content, bytes):
        raw = content
    elif isinstance(content, str):
        try:
            raw = content.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise GraderContractError("grader result is not valid UTF-8") from exc
    else:
        raise GraderContractError("grader result content type is invalid")
    if not raw or len(raw) > MAX_RESULT_BYTES:
        raise GraderContractError("grader result size is invalid")

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GraderContractError("grader result is not valid UTF-8") from exc
    if _contains_sensitive(text):
        raise GraderContractError(
            "grader result contains prohibited credential or reasoning content"
        )
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, RecursionError) as exc:
        raise GraderContractError("grader result is malformed") from exc

    data = _expect_object(value, TOP_LEVEL_KEYS, "grader result")
    canonical = json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    if raw != canonical:
        raise GraderContractError("grader result is not canonical JSON")
    if (
        type(data["schema_version"]) is not int
        or data["schema_version"] != SCHEMA_VERSION
    ):
        raise GraderContractError("grader result schema version is unsupported")

    checks_value = data["checks"]
    if not isinstance(checks_value, list) or not 1 <= len(checks_value) <= MAX_CHECKS:
        raise GraderContractError("grader result checks are invalid")
    checks = tuple(_parse_check(item) for item in checks_value)
    check_ids = tuple(check.check_id for check in checks)
    if check_ids != tuple(sorted(check_ids)) or len(set(check_ids)) != len(check_ids):
        raise GraderContractError("grader checks are unsorted or duplicated")

    outcome = _expect_enum(data["outcome"], OUTCOMES, "grader outcome")
    all_passed = all(check.outcome == "passed" for check in checks)
    if outcome == "passed" and not all_passed:
        raise GraderContractError("passed result contains a failed check")
    if outcome == "failed" and all_passed:
        raise GraderContractError("failed result contains no failed check")

    result = GraderResult(
        task_id=_expect_match(data["task_id"], _ID_RE, "task ID"),
        task_version=_expect_integer(
            data["task_version"],
            "task version",
            1,
            1_000_000_000,
        ),
        manifest_sha256=_expect_match(
            data["manifest_sha256"],
            _DIGEST_RE,
            "manifest digest",
        ),
        grader_sha256=_expect_match(
            data["grader_sha256"],
            _DIGEST_RE,
            "grader digest",
        ),
        foundation_sha=_expect_match(
            data["foundation_sha"],
            _SHA_RE,
            "Foundation SHA",
        ),
        base_sha=_expect_match(data["base_sha"], _SHA_RE, "base SHA"),
        candidate_sha=_expect_match(
            data["candidate_sha"],
            _SHA_RE,
            "candidate SHA",
        ),
        outcome=outcome,
        checks=checks,
        summary=_expect_summary(data["summary"]),
        result_sha256=hashlib.sha256(raw).hexdigest(),
    )

    expectation = _validate_expectation(expected)
    observed_identity = (
        result.task_id,
        result.task_version,
        result.manifest_sha256,
        result.grader_sha256,
        result.foundation_sha,
        result.base_sha,
        result.candidate_sha,
    )
    expected_identity = (
        expectation.task_id,
        expectation.task_version,
        expectation.manifest_sha256,
        expectation.grader_sha256,
        expectation.foundation_sha,
        expectation.base_sha,
        expectation.candidate_sha,
    )
    if observed_identity != expected_identity:
        raise GraderContractError("grader result identity does not match expectation")
    return result


def validate_grader_process_result(
    exit_code: int,
    result: GraderResult | None,
) -> GraderResult:
    """Validate process exit/result agreement without executing a process."""
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise GraderInfrastructureError("grader exit code is invalid")
    if exit_code not in {0, 1}:
        raise GraderInfrastructureError("grader process ended as infrastructure failure")
    if not isinstance(result, GraderResult):
        raise GraderInfrastructureError("grader process did not produce a valid result")
    expected_outcome = "passed" if exit_code == 0 else "failed"
    if result.outcome != expected_outcome:
        raise GraderInfrastructureError("grader exit code and result outcome disagree")
    return result


def _expect_absolute_argument(value: str | os.PathLike[str], label: str) -> str:
    try:
        text = os.fspath(value)
    except TypeError as exc:
        raise GraderContractError(f"{label} is invalid") from exc
    if (
        not isinstance(text, str)
        or not text
        or "\x00" in text
        or "\n" in text
        or "\r" in text
        or not Path(text).is_absolute()
        or any(part in {".", ".."} for part in Path(text).parts)
    ):
        raise GraderContractError(f"{label} must be an absolute path")
    return text


def _expect_entrypoint(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_PATH_LENGTH
        or value != value.strip()
        or not value.startswith("grader/")
        or value == "grader/"
        or value.startswith(("/", "\\"))
        or _WINDOWS_DRIVE_RE.match(value)
        or "\\" in value
        or "//" in value
        or _CONTROL_RE.search(value)
        or any(character in value for character in _GLOB_CHARACTERS)
    ):
        raise GraderContractError("grader entrypoint is invalid")
    if any(
        part in {"", ".", ".."} or part.casefold() == ".git"
        for part in value.split("/")
    ):
        raise GraderContractError("grader entrypoint is unsafe")
    return value


def build_grader_command(
    runtime: str,
    entrypoint: str,
    workspace: str | os.PathLike[str],
    result_path: str | os.PathLike[str],
) -> tuple[str, ...]:
    """Build exact argv for a runner; never execute or return a shell string."""
    if runtime not in RUNTIME_COMMANDS:
        raise GraderContractError("grader runtime is invalid")
    parsed_entrypoint = _expect_entrypoint(entrypoint)
    parsed_workspace = _expect_absolute_argument(workspace, "workspace")
    parsed_result = _expect_absolute_argument(result_path, "result path")
    workspace_path = Path(parsed_workspace)
    result = Path(parsed_result)
    if result == workspace_path or workspace_path in result.parents:
        raise GraderContractError("result path must be outside the workspace")
    return (
        RUNTIME_COMMANDS[runtime],
        parsed_entrypoint,
        "--workspace",
        parsed_workspace,
        "--result",
        parsed_result,
    )

#!/usr/bin/env python3
"""Parse immutable coding-agent evaluation task manifests."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 65_536
MAX_ID_LENGTH = 128
MAX_LABEL_LENGTH = 240
MAX_BODY_LENGTH = 16_384
MAX_PATH_LENGTH = 240
MAX_ALLOWED_PATHS = 128
MAX_PROHIBITED_EFFECTS = 64
MAX_REQUIRED_CHECKS = 32
MAX_TAGS = 32
MAX_FILES = 100_000
MAX_BUNDLE_BYTES = 10_737_418_240
MAX_TIMEOUT_SECONDS = 86_400
MAX_TRIAL_COUNT = 100

TOP_LEVEL_KEYS = frozenset({
    "schema_version", "task_id", "task_version", "category", "risk_tier",
    "fixture_bundle", "grader", "issue", "allowed_paths",
    "prohibited_effects", "required_checks", "protected_authorization",
    "expected_completion_class", "expected_human_action_reason",
    "trial_count", "environment_profile", "tags",
})
BUNDLE_KEYS = frozenset({"sha256", "file_count", "uncompressed_bytes"})
GRADER_KEYS = frozenset({
    "sha256", "runtime", "entrypoint", "timeout_seconds", "network_mode"
})
ISSUE_KEYS = frozenset({"title", "body"})
PROTECTED_AUTHORIZATION_KEYS = frozenset({
    "actor", "source", "required_marker", "expected_head_required"
})

CATEGORIES = frozenset({
    "bug_fix", "test_addition", "multi_file_change", "scope_trap",
    "protected_boundary", "stale_evidence", "provider_unavailable",
    "human_only", "handoff_resume",
})
RISK_TIERS = frozenset({"low", "standard", "protected"})
GRADER_RUNTIMES = frozenset({"python3.12", "python3.13", "node20", "bash"})
NETWORK_MODES = frozenset({"disabled", "allowlisted"})
AUTHORIZATION_SOURCES = frozenset({
    "issue_body", "issue_comment", "pull_request_review"
})
EXPECTED_COMPLETION_CLASSES = frozenset({
    "change_required", "no_change_required", "human_action_required"
})
HUMAN_ONLY_REASON_CODES = frozenset({
    "HUMAN_ONLY_ACCOUNT_LEVEL_REPOSITORY_CREATION_UI_UNAVAILABLE",
    "HUMAN_ONLY_CREDENTIAL_PROVIDER_UI_REQUIRED",
    "HUMAN_ONLY_DISCONNECTED_INTEGRATION_RECONNECTION_UI_REQUIRED",
})

_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,127})$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_MARKER_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_TAG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,63})$")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:/")
_SINGLE_LINE_RE = re.compile(r"^[^\x00-\x1f\x7f]+$")
_MULTILINE_CONTROL_RE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")
_GLOB_CHARS = frozenset("*?[]{}")
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


class EvaluationTaskError(ValueError):
    """The task manifest is malformed, unsafe, non-canonical, or inconsistent."""


@dataclass(frozen=True)
class BundleIdentity:
    sha256: str
    file_count: int
    uncompressed_bytes: int


@dataclass(frozen=True)
class GraderContract:
    sha256: str
    runtime: str
    entrypoint: str
    timeout_seconds: int
    network_mode: str


@dataclass(frozen=True)
class IssueContract:
    title: str
    body: str


@dataclass(frozen=True)
class ProtectedAuthorization:
    actor: str
    source: str
    required_marker: str
    expected_head_required: bool


@dataclass(frozen=True)
class EvaluationTask:
    task_id: str
    task_version: int
    category: str
    risk_tier: str
    fixture_bundle: BundleIdentity
    grader: GraderContract
    issue: IssueContract
    allowed_paths: tuple[str, ...]
    prohibited_effects: tuple[str, ...]
    required_checks: tuple[str, ...]
    protected_authorization: ProtectedAuthorization | None
    expected_completion_class: str
    expected_human_action_reason: str | None
    trial_count: int
    environment_profile: str
    tags: tuple[str, ...]
    manifest_sha256: str


def _duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvaluationTaskError("task manifest contains a duplicate JSON member")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise EvaluationTaskError(
        f"non-standard JSON numeric constant is not allowed: {value}"
    )


def _raw_bytes(content: bytes | str) -> bytes:
    if isinstance(content, bytes):
        raw = content
    elif isinstance(content, str):
        try:
            raw = content.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise EvaluationTaskError("task manifest is not valid UTF-8") from exc
    else:
        raise EvaluationTaskError("task manifest content type is invalid")
    if not raw or len(raw) > MAX_MANIFEST_BYTES:
        raise EvaluationTaskError("task manifest size is invalid")
    return raw


def _contains_sensitive(text: str) -> bool:
    return any(pattern.search(text) for pattern in _SENSITIVE_PATTERNS)


def _object(value: Any, keys: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise EvaluationTaskError(f"{label} keys are invalid")
    return value


def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise EvaluationTaskError(f"{label} is invalid")
    return value


def _enum(value: Any, values: frozenset[str], label: str) -> str:
    if not isinstance(value, str) or value not in values:
        raise EvaluationTaskError(f"{label} is invalid")
    return value


def _match(value: Any, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise EvaluationTaskError(f"{label} is invalid")
    return value


def _single_line(value: Any, label: str, maximum: int = MAX_LABEL_LENGTH) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or value != value.strip()
        or _SINGLE_LINE_RE.fullmatch(value) is None
        or _contains_sensitive(value)
    ):
        raise EvaluationTaskError(f"{label} is invalid or prohibited")
    return value


def _multiline(value: Any, label: str, maximum: int = MAX_BODY_LENGTH) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or value != value.strip()
        or "\r" in value
        or _MULTILINE_CONTROL_RE.search(value)
        or _contains_sensitive(value)
    ):
        raise EvaluationTaskError(f"{label} is invalid or prohibited")
    return value


def _digest(value: Any, label: str) -> str:
    return _match(value, _DIGEST_RE, label)


def _exact_path(value: Any, label: str = "repository path") -> str:
    path = _single_line(value, label, MAX_PATH_LENGTH)
    parts = path.split("/")
    if (
        path.startswith(("/", "\\", "./", "../"))
        or _WINDOWS_DRIVE_RE.match(path)
        or "\\" in path
        or "//" in path
        or any(char in path for char in _GLOB_CHARS)
        or any(
            part in {"", ".", ".."} or part.casefold() == ".git"
            for part in parts
        )
    ):
        raise EvaluationTaskError(f"{label} is unsafe or not exact")
    return path


def _allowed_path(value: Any) -> tuple[str, bool]:
    if not isinstance(value, str):
        raise EvaluationTaskError("allowed path is invalid")
    is_scope = value.endswith("/**")
    candidate = value[:-3] if is_scope else value
    parsed = _exact_path(candidate, "allowed path")
    return (parsed + "/**" if is_scope else parsed, is_scope)


def _unique_strings(
    values: Any,
    *,
    label: str,
    maximum: int,
    parser,
    minimum: int = 1,
    casefold: bool = False,
) -> tuple[str, ...]:
    if (
        not isinstance(values, list)
        or not minimum <= len(values) <= maximum
    ):
        raise EvaluationTaskError(f"{label} is invalid")
    parsed = tuple(parser(item) for item in values)
    identities = tuple(
        item.casefold() if casefold else item for item in parsed
    )
    if len(set(identities)) != len(identities):
        raise EvaluationTaskError(f"{label} contains duplicates")
    return parsed


def _bundle(value: Any) -> BundleIdentity:
    bundle = _object(value, BUNDLE_KEYS, "fixture bundle")
    return BundleIdentity(
        _digest(bundle["sha256"], "fixture bundle digest"),
        _integer(bundle["file_count"], "fixture file count", 1, MAX_FILES),
        _integer(
            bundle["uncompressed_bytes"],
            "fixture uncompressed bytes",
            1,
            MAX_BUNDLE_BYTES,
        ),
    )


def _grader(value: Any) -> GraderContract:
    grader = _object(value, GRADER_KEYS, "grader")
    entrypoint = _exact_path(grader["entrypoint"], "grader entrypoint")
    if not entrypoint.startswith("grader/") or entrypoint == "grader":
        raise EvaluationTaskError("grader entrypoint must be below grader/")
    return GraderContract(
        _digest(grader["sha256"], "grader digest"),
        _enum(grader["runtime"], GRADER_RUNTIMES, "grader runtime"),
        entrypoint,
        _integer(
            grader["timeout_seconds"],
            "grader timeout",
            1,
            MAX_TIMEOUT_SECONDS,
        ),
        _enum(
            grader["network_mode"], NETWORK_MODES, "grader network mode"
        ),
    )


def _issue(value: Any) -> IssueContract:
    issue = _object(value, ISSUE_KEYS, "Issue contract")
    return IssueContract(
        _single_line(issue["title"], "Issue title", 240),
        _multiline(issue["body"], "Issue body"),
    )


def _protected_authorization(
    value: Any,
) -> ProtectedAuthorization | None:
    if value is None:
        return None
    authorization = _object(
        value, PROTECTED_AUTHORIZATION_KEYS, "protected authorization"
    )
    expected_head = authorization["expected_head_required"]
    if expected_head is not True:
        raise EvaluationTaskError(
            "protected authorization must require expected head"
        )
    return ProtectedAuthorization(
        _match(authorization["actor"], _LOGIN_RE, "authorization actor"),
        _enum(
            authorization["source"],
            AUTHORIZATION_SOURCES,
            "authorization source",
        ),
        _match(
            authorization["required_marker"],
            _MARKER_RE,
            "authorization marker",
        ),
        expected_head,
    )


def parse_evaluation_task(content: bytes | str) -> EvaluationTask:
    """Return one immutable canonical task manifest or fail closed."""
    raw = _raw_bytes(content)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvaluationTaskError("task manifest is not valid UTF-8") from exc
    if _contains_sensitive(text):
        raise EvaluationTaskError(
            "task manifest contains prohibited credential or reasoning content"
        )
    try:
        value = json.loads(
            text,
            object_pairs_hook=_duplicates,
            parse_constant=_constant,
        )
    except (json.JSONDecodeError, RecursionError) as exc:
        raise EvaluationTaskError("task manifest is malformed") from exc
    manifest = _object(value, TOP_LEVEL_KEYS, "task manifest")
    canonical = json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    if raw != canonical:
        raise EvaluationTaskError("task manifest is not canonical JSON")
    if (
        type(manifest["schema_version"]) is not int
        or manifest["schema_version"] != SCHEMA_VERSION
    ):
        raise EvaluationTaskError(
            "task manifest schema version is unsupported"
        )

    allowed_entries = manifest["allowed_paths"]
    if (
        not isinstance(allowed_entries, list)
        or not 1 <= len(allowed_entries) <= MAX_ALLOWED_PATHS
    ):
        raise EvaluationTaskError("allowed paths are invalid")
    allowed: list[str] = []
    scope_count = 0
    for item in allowed_entries:
        parsed, is_scope = _allowed_path(item)
        allowed.append(parsed)
        scope_count += int(is_scope)
    if len(set(allowed)) != len(allowed):
        raise EvaluationTaskError("allowed paths contain duplicates")
    if scope_count > 1:
        raise EvaluationTaskError(
            "allowed paths contain more than one scope pattern"
        )

    risk_tier = _enum(manifest["risk_tier"], RISK_TIERS, "risk tier")
    authorization = _protected_authorization(
        manifest["protected_authorization"]
    )
    if risk_tier == "protected" and authorization is None:
        raise EvaluationTaskError(
            "protected task requires authorization contract"
        )
    if risk_tier != "protected" and authorization is not None:
        raise EvaluationTaskError(
            "non-protected task must not carry authorization"
        )

    completion = _enum(
        manifest["expected_completion_class"],
        EXPECTED_COMPLETION_CLASSES,
        "expected completion class",
    )
    human_reason = manifest["expected_human_action_reason"]
    if human_reason is not None:
        human_reason = _enum(
            human_reason,
            HUMAN_ONLY_REASON_CODES,
            "expected human action reason",
        )
    if completion == "human_action_required":
        if human_reason is None:
            raise EvaluationTaskError(
                "human-action completion requires an audited human-only reason"
            )
    elif human_reason is not None:
        raise EvaluationTaskError(
            "non-human completion must not carry a human-only reason"
        )

    prohibited = _unique_strings(
        manifest["prohibited_effects"],
        label="prohibited effects",
        maximum=MAX_PROHIBITED_EFFECTS,
        parser=lambda item: _single_line(
            item, "prohibited effect", 500
        ),
        casefold=True,
    )
    checks = _unique_strings(
        manifest["required_checks"],
        label="required checks",
        maximum=MAX_REQUIRED_CHECKS,
        parser=lambda item: _single_line(item, "required check", 160),
        casefold=True,
    )
    tags = _unique_strings(
        manifest["tags"],
        label="tags",
        maximum=MAX_TAGS,
        parser=lambda item: _match(item, _TAG_RE, "tag"),
    )

    return EvaluationTask(
        task_id=_match(manifest["task_id"], _ID_RE, "task ID"),
        task_version=_integer(
            manifest["task_version"],
            "task version",
            1,
            1_000_000_000,
        ),
        category=_enum(
            manifest["category"], CATEGORIES, "task category"
        ),
        risk_tier=risk_tier,
        fixture_bundle=_bundle(manifest["fixture_bundle"]),
        grader=_grader(manifest["grader"]),
        issue=_issue(manifest["issue"]),
        allowed_paths=tuple(allowed),
        prohibited_effects=prohibited,
        required_checks=checks,
        protected_authorization=authorization,
        expected_completion_class=completion,
        expected_human_action_reason=human_reason,
        trial_count=_integer(
            manifest["trial_count"],
            "trial count",
            1,
            MAX_TRIAL_COUNT,
        ),
        environment_profile=_match(
            manifest["environment_profile"],
            _ID_RE,
            "environment profile",
        ),
        tags=tags,
        manifest_sha256=hashlib.sha256(raw).hexdigest(),
    )

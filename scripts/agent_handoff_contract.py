#!/usr/bin/env python3
"""Validate a bounded exact-SHA cross-session agent handoff bundle."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = 1
MAX_TASK_STATE_BYTES = 32_768
MAX_DECISIONS_BYTES = 65_536
MAX_HANDOFF_BYTES = 32_768
MAX_DECISION_LINE_BYTES = 8_192
MAX_DECISIONS = 200
MAX_WORK_ITEMS = 100
MAX_PATHS = 200
MAX_BLOCKERS = 20
MAX_TEXT_LENGTH = 500
MAX_DETAIL_LENGTH = 1_000
MAX_PATH_LENGTH = 240
MAX_COUNT = 1_000_000_000

TASK_STATE_KEYS = frozenset({
    "schema_version",
    "task_id",
    "repository",
    "issue_number",
    "base_sha",
    "candidate_sha",
    "updated_at",
    "phase",
    "completed_work",
    "pending_work",
    "read_paths",
    "changed_paths",
    "next_action",
    "blockers",
    "human_action_required",
    "decision_count",
    "decisions_sha256",
    "handoff_sha256",
})
DECISION_KEYS = frozenset({
    "schema_version",
    "decision_id",
    "repository",
    "issue_number",
    "recorded_head_sha",
    "recorded_at",
    "summary",
    "rationale",
    "supersedes",
})
BLOCKER_KEYS = frozenset({"code", "detail"})
PHASES = frozenset({
    "planning",
    "implementation",
    "validation",
    "review",
    "blocked",
    "completed",
})
HUMAN_ONLY_REASON_CODES = frozenset({
    "HUMAN_ONLY_ACCOUNT_LEVEL_REPOSITORY_CREATION_UI_UNAVAILABLE",
    "HUMAN_ONLY_CREDENTIAL_PROVIDER_UI_REQUIRED",
    "HUMAN_ONLY_DISCONNECTED_INTEGRATION_RECONNECTION_UI_REQUIRED",
})
_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,127})$")
_REPOSITORY_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/"
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})$"
)
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_TEXT_RE = re.compile(r"^[^\x00-\x1f\x7f]+$")
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_MARKDOWN_CONTROL_RE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")
_MARKER_RE = re.compile(
    r"\A# Agent handoff\n\n"
    r"<!-- foundation-agent-handoff\n"
    r"schema_version: 1\n"
    r"repository: (?P<repository>[^\n]+)\n"
    r"issue_number: (?P<issue_number>[1-9][0-9]*)\n"
    r"candidate_sha: (?P<candidate_sha>[0-9a-f]{40})\n"
    r"-->\n\n"
)
_SENSITIVE_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.IGNORECASE),
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


class HandoffContractError(ValueError):
    """The handoff bundle is malformed, stale, unbounded, or inconsistent."""


@dataclass(frozen=True, order=True)
class Blocker:
    code: str
    detail: str


@dataclass(frozen=True)
class TaskState:
    task_id: str
    repository: str
    issue_number: int
    base_sha: str
    candidate_sha: str
    updated_at: datetime
    phase: str
    completed_work: tuple[str, ...]
    pending_work: tuple[str, ...]
    read_paths: tuple[str, ...]
    changed_paths: tuple[str, ...]
    next_action: str
    blockers: tuple[Blocker, ...]
    human_action_required: bool
    decision_count: int
    decisions_sha256: str
    handoff_sha256: str


@dataclass(frozen=True)
class DecisionRecord:
    decision_id: str
    repository: str
    issue_number: int
    recorded_head_sha: str
    recorded_at: datetime
    summary: str
    rationale: str
    supersedes: str | None


@dataclass(frozen=True)
class HandoffDocument:
    repository: str
    issue_number: int
    candidate_sha: str
    current_status: str
    next_action: str
    blocker_text: str
    content_sha256: str


@dataclass(frozen=True)
class HandoffBundle:
    state: TaskState
    decisions: tuple[DecisionRecord, ...]
    active_decision_ids: tuple[str, ...]
    handoff: HandoffDocument


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise HandoffContractError("handoff artifact contains a duplicate JSON member")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise HandoffContractError(f"non-standard JSON numeric constant is not allowed: {value}")


def _to_bytes(content: bytes | str, *, label: str, maximum: int, allow_empty: bool = False) -> bytes:
    if isinstance(content, bytes):
        raw = content
    elif isinstance(content, str):
        try:
            raw = content.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise HandoffContractError(f"{label} is not valid UTF-8") from exc
    else:
        raise HandoffContractError(f"{label} content type is invalid")
    if len(raw) > maximum or (not raw and not allow_empty):
        raise HandoffContractError(f"{label} size is invalid")
    return raw


def _json_object(raw: bytes, *, keys: frozenset[str], label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise HandoffContractError(f"{label} is malformed") from exc
    if not isinstance(value, dict) or set(value) != keys:
        raise HandoffContractError(f"{label} keys are invalid")
    return value


def _strict_schema_version(value: Any, *, label: str) -> None:
    if type(value) is not int or value != SCHEMA_VERSION:
        raise HandoffContractError(f"{label} schema version is unsupported")


def _integer(value: Any, *, label: str, minimum: int = 0, maximum: int = MAX_COUNT) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise HandoffContractError(f"{label} is invalid")
    return value


def _decimal_integer(value: str, *, label: str, minimum: int = 0, maximum: int = MAX_COUNT) -> int:
    if not value.isascii() or not value.isdigit() or len(value) > len(str(maximum)):
        raise HandoffContractError(f"{label} is invalid")
    return _integer(int(value), label=label, minimum=minimum, maximum=maximum)


def _identity(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise HandoffContractError(f"{label} is invalid")
    return value


def _repository(value: Any) -> str:
    if not isinstance(value, str) or _REPOSITORY_RE.fullmatch(value) is None:
        raise HandoffContractError("repository identity is invalid")
    owner, name = value.split("/", 1)
    if owner in {".", ".."} or name in {".", ".."} or name.endswith(".git"):
        raise HandoffContractError("repository identity is invalid")
    return value


def _sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise HandoffContractError(f"{label} is invalid")
    return value


def _digest(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise HandoffContractError(f"{label} is invalid")
    return value


def _timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or _UTC_RE.fullmatch(value) is None:
        raise HandoffContractError(f"{label} is invalid")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise HandoffContractError(f"{label} is invalid") from exc


def _text(value: Any, *, label: str, maximum: int = MAX_TEXT_LENGTH) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or value != value.strip()
        or _TEXT_RE.fullmatch(value) is None
    ):
        raise HandoffContractError(f"{label} is invalid")
    _reject_sensitive(value, label=label)
    return value


def _path(value: Any) -> str:
    result = _text(value, label="repository path", maximum=MAX_PATH_LENGTH)
    if (
        result.startswith(("/", "\\", "./", "../"))
        or "\\" in result
        or "//" in result
        or any(part in {"", ".", ".."} for part in result.split("/"))
    ):
        raise HandoffContractError("repository path is unsafe")
    return result


def _unique_text_list(value: Any, *, label: str, maximum: int = MAX_WORK_ITEMS) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise HandoffContractError(f"{label} is invalid")
    result = tuple(_text(item, label=label) for item in value)
    if len(set(result)) != len(result):
        raise HandoffContractError(f"{label} contains duplicates")
    return result


def _unique_path_list(value: Any, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > MAX_PATHS:
        raise HandoffContractError(f"{label} is invalid")
    result = tuple(_path(item) for item in value)
    if len(set(result)) != len(result):
        raise HandoffContractError(f"{label} contains duplicates")
    return result


def _reject_sensitive(content: bytes | str, *, label: str) -> None:
    if isinstance(content, bytes):
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HandoffContractError(f"{label} is not valid UTF-8") from exc
    else:
        text = content
    for pattern in _SENSITIVE_PATTERNS:
        if pattern.search(text):
            raise HandoffContractError(f"{label} contains prohibited sensitive or reasoning content")


def _parse_blockers(value: Any) -> tuple[Blocker, ...]:
    if not isinstance(value, list) or len(value) > MAX_BLOCKERS:
        raise HandoffContractError("blockers are invalid")
    blockers: list[Blocker] = []
    codes: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != BLOCKER_KEYS:
            raise HandoffContractError("blocker keys are invalid")
        code = item["code"]
        if not isinstance(code, str) or _CODE_RE.fullmatch(code) is None:
            raise HandoffContractError("blocker code is invalid")
        if code in codes:
            raise HandoffContractError("blocker code is duplicated")
        codes.add(code)
        blockers.append(Blocker(code=code, detail=_text(
            item["detail"], label="blocker detail", maximum=MAX_DETAIL_LENGTH
        )))
    return tuple(blockers)


def parse_task_state(content: bytes | str) -> TaskState:
    raw = _to_bytes(content, label="task state", maximum=MAX_TASK_STATE_BYTES)
    _reject_sensitive(raw, label="task state")
    value = _json_object(raw, keys=TASK_STATE_KEYS, label="task state")
    _strict_schema_version(value["schema_version"], label="task state")
    phase = value["phase"]
    if not isinstance(phase, str) or phase not in PHASES:
        raise HandoffContractError("task phase is invalid")
    completed = _unique_text_list(value["completed_work"], label="completed work")
    pending = _unique_text_list(value["pending_work"], label="pending work")
    if set(completed) & set(pending):
        raise HandoffContractError("completed and pending work overlap")
    blockers = _parse_blockers(value["blockers"])
    human_required = value["human_action_required"]
    if not isinstance(human_required, bool):
        raise HandoffContractError("human action flag is invalid")
    blocker_codes = {item.code for item in blockers}
    if blockers and phase != "blocked":
        raise HandoffContractError("technical blockers require blocked phase")
    if phase == "blocked" and not blockers:
        raise HandoffContractError("blocked phase requires blocker evidence")
    if human_required:
        if len(blockers) != 1 or not blocker_codes <= HUMAN_ONLY_REASON_CODES:
            raise HandoffContractError("human action requires one audited human-only reason")
    elif blocker_codes & HUMAN_ONLY_REASON_CODES:
        raise HandoffContractError("human-only blocker requires human action flag")
    next_action = _text(value["next_action"], label="next automatic action")
    if phase == "completed":
        if pending or blockers or human_required or next_action != "none":
            raise HandoffContractError("completed phase invariants are invalid")
    elif next_action == "none":
        raise HandoffContractError("active phase requires a next automatic action")
    return TaskState(
        task_id=_identity(value["task_id"], label="task ID"),
        repository=_repository(value["repository"]),
        issue_number=_integer(value["issue_number"], label="Issue number", minimum=1),
        base_sha=_sha(value["base_sha"], label="base SHA"),
        candidate_sha=_sha(value["candidate_sha"], label="candidate SHA"),
        updated_at=_timestamp(value["updated_at"], label="state update time"),
        phase=phase,
        completed_work=completed,
        pending_work=pending,
        read_paths=_unique_path_list(value["read_paths"], label="read paths"),
        changed_paths=_unique_path_list(value["changed_paths"], label="changed paths"),
        next_action=next_action,
        blockers=blockers,
        human_action_required=human_required,
        decision_count=_integer(
            value["decision_count"], label="decision count", minimum=0,
            maximum=MAX_DECISIONS,
        ),
        decisions_sha256=_digest(value["decisions_sha256"], label="decisions digest"),
        handoff_sha256=_digest(value["handoff_sha256"], label="handoff digest"),
    )


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def parse_decisions(
    content: bytes | str,
    *,
    state: TaskState,
) -> tuple[tuple[DecisionRecord, ...], tuple[str, ...]]:
    raw = _to_bytes(
        content, label="decisions JSONL", maximum=MAX_DECISIONS_BYTES,
        allow_empty=True,
    )
    _reject_sensitive(raw, label="decisions JSONL")
    if hashlib.sha256(raw).hexdigest() != state.decisions_sha256:
        raise HandoffContractError("decisions digest does not match task state")
    if not raw:
        if state.decision_count != 0:
            raise HandoffContractError("decision count does not match empty JSONL")
        return (), ()
    if not raw.endswith(b"\n") or b"\r" in raw:
        raise HandoffContractError("decisions JSONL must use LF and end with a newline")
    lines = raw[:-1].split(b"\n")
    if not lines or len(lines) > MAX_DECISIONS or any(not line for line in lines):
        raise HandoffContractError("decisions JSONL line structure is invalid")
    if len(lines) != state.decision_count:
        raise HandoffContractError("decision count does not match task state")

    records: list[DecisionRecord] = []
    identities: set[str] = set()
    active: set[str] = set()
    superseded_targets: set[str] = set()
    previous_time: datetime | None = None
    for line in lines:
        if len(line) > MAX_DECISION_LINE_BYTES:
            raise HandoffContractError("decision JSONL line is too large")
        value = _json_object(line, keys=DECISION_KEYS, label="decision record")
        if _canonical_json(value) != line:
            raise HandoffContractError("decision record is not canonical JSON")
        _strict_schema_version(value["schema_version"], label="decision record")
        decision_id = _identity(value["decision_id"], label="decision ID")
        if decision_id in identities:
            raise HandoffContractError("decision identity is duplicated")
        repository = _repository(value["repository"])
        issue_number = _integer(value["issue_number"], label="decision Issue number", minimum=1)
        if repository != state.repository or issue_number != state.issue_number:
            raise HandoffContractError("decision record belongs to another task")
        recorded_at = _timestamp(value["recorded_at"], label="decision timestamp")
        if previous_time is not None and recorded_at < previous_time:
            raise HandoffContractError("decision timestamps are not append ordered")
        previous_time = recorded_at
        supersedes = value["supersedes"]
        if supersedes is not None:
            supersedes = _identity(supersedes, label="superseded decision ID")
            if supersedes not in active or supersedes in superseded_targets:
                raise HandoffContractError("decision supersession target is not active")
            active.remove(supersedes)
            superseded_targets.add(supersedes)
        identities.add(decision_id)
        active.add(decision_id)
        records.append(DecisionRecord(
            decision_id=decision_id,
            repository=repository,
            issue_number=issue_number,
            recorded_head_sha=_sha(
                value["recorded_head_sha"], label="decision recorded head SHA"
            ),
            recorded_at=recorded_at,
            summary=_text(value["summary"], label="decision summary"),
            rationale=_text(
                value["rationale"], label="decision rationale", maximum=MAX_DETAIL_LENGTH
            ),
            supersedes=supersedes,
        ))
    return tuple(records), tuple(sorted(active))


def _expected_blocker_text(blockers: tuple[Blocker, ...]) -> str:
    if not blockers:
        return "None."
    return "\n".join(f"- {item.code}: {item.detail}" for item in blockers)


def parse_handoff_document(content: bytes | str, *, state: TaskState) -> HandoffDocument:
    raw = _to_bytes(content, label="handoff Markdown", maximum=MAX_HANDOFF_BYTES)
    _reject_sensitive(raw, label="handoff Markdown")
    if hashlib.sha256(raw).hexdigest() != state.handoff_sha256:
        raise HandoffContractError("handoff digest does not match task state")
    if b"\r" in raw or not raw.endswith(b"\n"):
        raise HandoffContractError("handoff Markdown must use LF and end with a newline")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HandoffContractError("handoff Markdown is not valid UTF-8") from exc
    if _MARKDOWN_CONTROL_RE.search(text):
        raise HandoffContractError("handoff Markdown contains a prohibited control character")
    marker = _MARKER_RE.match(text)
    if marker is None:
        raise HandoffContractError("handoff metadata marker is invalid")
    repository = _repository(marker.group("repository"))
    issue_number = _decimal_integer(
        marker.group("issue_number"), label="handoff Issue number", minimum=1
    )
    candidate_sha = _sha(marker.group("candidate_sha"), label="handoff candidate SHA")
    if (
        repository != state.repository
        or issue_number != state.issue_number
        or candidate_sha != state.candidate_sha
    ):
        raise HandoffContractError("handoff metadata does not match task state")

    body = text[marker.end():]
    headings = (
        "## Current status\n\n",
        "## Next automatic action\n\n",
        "## Technical blockers\n\n",
    )
    if any(body.count(heading) != 1 for heading in headings):
        raise HandoffContractError("handoff Markdown headings are invalid")
    first = body.index(headings[0])
    second = body.index(headings[1])
    third = body.index(headings[2])
    if first != 0 or not first < second < third:
        raise HandoffContractError("handoff Markdown section order is invalid")
    current_status = body[len(headings[0]):second].strip()
    next_action = body[second + len(headings[1]):third].strip()
    blocker_text = body[third + len(headings[2]):].strip()
    if not current_status or len(current_status) > 4_000:
        raise HandoffContractError("handoff current status is invalid")
    _reject_sensitive(current_status, label="handoff current status")
    if next_action != state.next_action:
        raise HandoffContractError("handoff next action does not match task state")
    expected_blockers = _expected_blocker_text(state.blockers)
    if blocker_text != expected_blockers:
        raise HandoffContractError("handoff blocker text does not match task state")
    return HandoffDocument(
        repository=repository,
        issue_number=issue_number,
        candidate_sha=candidate_sha,
        current_status=current_status,
        next_action=next_action,
        blocker_text=blocker_text,
        content_sha256=hashlib.sha256(raw).hexdigest(),
    )


def parse_handoff_bundle(
    task_state: bytes | str,
    decisions_jsonl: bytes | str,
    handoff_markdown: bytes | str,
    *,
    expected_repository: str,
    expected_issue_number: int,
    expected_base_sha: str,
    expected_candidate_sha: str,
) -> HandoffBundle:
    """Return one immutable consistent bundle or reject stale/inconsistent state."""
    live_repository = _repository(expected_repository)
    live_issue = _integer(expected_issue_number, label="expected Issue number", minimum=1)
    live_base = _sha(expected_base_sha, label="expected base SHA")
    live_head = _sha(expected_candidate_sha, label="expected candidate SHA")
    state = parse_task_state(task_state)
    if (
        state.repository != live_repository
        or state.issue_number != live_issue
        or state.base_sha != live_base
        or state.candidate_sha != live_head
    ):
        raise HandoffContractError("handoff bundle is stale or belongs to another task")
    decisions, active = parse_decisions(decisions_jsonl, state=state)
    if decisions and state.updated_at < decisions[-1].recorded_at:
        raise HandoffContractError("task state predates the latest decision")
    handoff = parse_handoff_document(handoff_markdown, state=state)
    return HandoffBundle(
        state=state,
        decisions=decisions,
        active_decision_ids=active,
        handoff=handoff,
    )

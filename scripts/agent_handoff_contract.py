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
    "schema_version", "task_id", "repository", "issue_number", "base_sha",
    "candidate_sha", "updated_at", "phase", "completed_work", "pending_work",
    "read_paths", "changed_paths", "next_action", "blockers",
    "human_action_required", "decision_count", "decisions_sha256", "handoff_sha256",
})
DECISION_KEYS = frozenset({
    "schema_version", "decision_id", "repository", "issue_number",
    "recorded_head_sha", "recorded_at", "summary", "rationale", "supersedes",
})
BLOCKER_KEYS = frozenset({"code", "detail"})
PHASES = frozenset({"planning", "implementation", "validation", "review", "blocked", "completed"})
HUMAN_ONLY_REASON_CODES = frozenset({
    "HUMAN_ONLY_ACCOUNT_LEVEL_REPOSITORY_CREATION_UI_UNAVAILABLE",
    "HUMAN_ONLY_CREDENTIAL_PROVIDER_UI_REQUIRED",
    "HUMAN_ONLY_DISCONNECTED_INTEGRATION_RECONNECTION_UI_REQUIRED",
})

_ID = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,127})$")
_REPO = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_TEXT = re.compile(r"^[^\x00-\x1f\x7f]+$")
_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:/")
_MARKDOWN_CONTROL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")
_MARKER = re.compile(
    r"\A# Agent handoff\n\n<!-- foundation-agent-handoff\n"
    r"schema_version: 1\nrepository: (?P<repository>[^\n]+)\n"
    r"issue_number: (?P<issue_number>[1-9][0-9]*)\n"
    r"candidate_sha: (?P<candidate_sha>[0-9a-f]{40})\n-->\n\n"
)
_SENSITIVE = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|secret|password)\b\s*[:=]\s*[\"']?[^\s\"'`]{8,}"),
    re.compile(r"(?i)<(?:thinking|analysis)>"),
    re.compile(r"(?i)\bchain[- ]of[- ]thought\s*:"),
    re.compile(r"(?i)\bprivate reasoning\s*:"),
)
_GLOB = frozenset("*?[]{}")


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


def _duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise HandoffContractError("handoff artifact contains a duplicate JSON member")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise HandoffContractError(f"non-standard JSON numeric constant is not allowed: {value}")


def _bytes(value: bytes | str, label: str, limit: int, *, empty: bool = False) -> bytes:
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, str):
        try:
            raw = value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise HandoffContractError(f"{label} is not valid UTF-8") from exc
    else:
        raise HandoffContractError(f"{label} content type is invalid")
    if len(raw) > limit or (not raw and not empty):
        raise HandoffContractError(f"{label} size is invalid")
    return raw


def _object(raw: bytes, keys: frozenset[str], label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_duplicates, parse_constant=_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise HandoffContractError(f"{label} is malformed") from exc
    if not isinstance(value, dict) or set(value) != keys:
        raise HandoffContractError(f"{label} keys are invalid")
    return value


def _sensitive(value: bytes | str, label: str) -> None:
    try:
        text = value.decode("utf-8") if isinstance(value, bytes) else value
    except UnicodeDecodeError as exc:
        raise HandoffContractError(f"{label} is not valid UTF-8") from exc
    if any(pattern.search(text) for pattern in _SENSITIVE):
        raise HandoffContractError(f"{label} contains prohibited sensitive or reasoning content")


def _integer(value: Any, label: str, minimum: int = 0, maximum: int = MAX_COUNT) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise HandoffContractError(f"{label} is invalid")
    return value


def _match(value: Any, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise HandoffContractError(f"{label} is invalid")
    return value


def _repository(value: Any) -> str:
    result = _match(value, _REPO, "repository identity")
    owner, name = result.split("/", 1)
    if owner in {".", ".."} or name in {".", ".."} or name.endswith(".git"):
        raise HandoffContractError("repository identity is invalid")
    return result


def _timestamp(value: Any, label: str) -> datetime:
    text = _match(value, _UTC, label)
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise HandoffContractError(f"{label} is invalid") from exc


def _text(value: Any, label: str, maximum: int = MAX_TEXT_LENGTH) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or value != value.strip() or _TEXT.fullmatch(value) is None:
        raise HandoffContractError(f"{label} is invalid")
    _sensitive(value, label)
    return value


def _path(value: Any) -> str:
    result = _text(value, "repository path", MAX_PATH_LENGTH)
    parts = result.split("/")
    if (
        result.startswith(("/", "\\", "./", "../"))
        or _WINDOWS_DRIVE.match(result)
        or "\\" in result
        or "//" in result
        or any(char in result for char in _GLOB)
        or any(part in {"", ".", ".."} or part.casefold() == ".git" for part in parts)
    ):
        raise HandoffContractError("repository path is unsafe or not exact")
    return result


def _unique(values: Any, label: str, limit: int, parser) -> tuple[str, ...]:
    if not isinstance(values, list) or len(values) > limit:
        raise HandoffContractError(f"{label} is invalid")
    result = tuple(parser(item) for item in values)
    if len(set(result)) != len(result):
        raise HandoffContractError(f"{label} contains duplicates")
    return result


def _blockers(values: Any) -> tuple[Blocker, ...]:
    if not isinstance(values, list) or len(values) > MAX_BLOCKERS:
        raise HandoffContractError("blockers are invalid")
    result: list[Blocker] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, dict) or set(item) != BLOCKER_KEYS:
            raise HandoffContractError("blocker keys are invalid")
        code = _match(item["code"], _CODE, "blocker code")
        if code in seen:
            raise HandoffContractError("blocker code is duplicated")
        seen.add(code)
        result.append(Blocker(code, _text(item["detail"], "blocker detail", MAX_DETAIL_LENGTH)))
    return tuple(result)


def parse_task_state(content: bytes | str) -> TaskState:
    raw = _bytes(content, "task state", MAX_TASK_STATE_BYTES)
    _sensitive(raw, "task state")
    value = _object(raw, TASK_STATE_KEYS, "task state")
    if type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION:
        raise HandoffContractError("task state schema version is unsupported")
    phase = value["phase"]
    if not isinstance(phase, str) or phase not in PHASES:
        raise HandoffContractError("task phase is invalid")
    completed = _unique(value["completed_work"], "completed work", MAX_WORK_ITEMS, lambda item: _text(item, "completed work"))
    pending = _unique(value["pending_work"], "pending work", MAX_WORK_ITEMS, lambda item: _text(item, "pending work"))
    if set(completed) & set(pending):
        raise HandoffContractError("completed and pending work overlap")
    blockers = _blockers(value["blockers"])
    human = value["human_action_required"]
    if not isinstance(human, bool):
        raise HandoffContractError("human action flag is invalid")
    codes = {item.code for item in blockers}
    if bool(blockers) != (phase == "blocked"):
        raise HandoffContractError("blocked phase and blocker evidence disagree")
    if human and (len(blockers) != 1 or not codes <= HUMAN_ONLY_REASON_CODES):
        raise HandoffContractError("human action requires one audited human-only reason")
    if not human and codes & HUMAN_ONLY_REASON_CODES:
        raise HandoffContractError("human-only blocker requires human action flag")
    next_action = _text(value["next_action"], "next automatic action")
    if phase == "completed":
        if pending or blockers or human or next_action != "none":
            raise HandoffContractError("completed phase invariants are invalid")
    elif next_action == "none":
        raise HandoffContractError("active phase requires a next automatic action")
    return TaskState(
        _match(value["task_id"], _ID, "task ID"), _repository(value["repository"]),
        _integer(value["issue_number"], "Issue number", 1),
        _match(value["base_sha"], _SHA, "base SHA"),
        _match(value["candidate_sha"], _SHA, "candidate SHA"),
        _timestamp(value["updated_at"], "state update time"), phase, completed, pending,
        _unique(value["read_paths"], "read paths", MAX_PATHS, _path),
        _unique(value["changed_paths"], "changed paths", MAX_PATHS, _path),
        next_action, blockers, human,
        _integer(value["decision_count"], "decision count", 0, MAX_DECISIONS),
        _match(value["decisions_sha256"], _DIGEST, "decisions digest"),
        _match(value["handoff_sha256"], _DIGEST, "handoff digest"),
    )


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def parse_decisions(content: bytes | str, state: TaskState) -> tuple[tuple[DecisionRecord, ...], tuple[str, ...]]:
    raw = _bytes(content, "decisions JSONL", MAX_DECISIONS_BYTES, empty=True)
    _sensitive(raw, "decisions JSONL")
    if hashlib.sha256(raw).hexdigest() != state.decisions_sha256:
        raise HandoffContractError("decisions digest does not match task state")
    if not raw:
        if state.decision_count:
            raise HandoffContractError("decision count does not match empty JSONL")
        return (), ()
    if b"\r" in raw or not raw.endswith(b"\n"):
        raise HandoffContractError("decisions JSONL must use LF and end with a newline")
    lines = raw[:-1].split(b"\n")
    if not lines or len(lines) > MAX_DECISIONS or any(not line for line in lines) or len(lines) != state.decision_count:
        raise HandoffContractError("decisions JSONL line structure or count is invalid")
    records: list[DecisionRecord] = []
    seen: set[str] = set()
    active: set[str] = set()
    previous: datetime | None = None
    for line in lines:
        if len(line) > MAX_DECISION_LINE_BYTES:
            raise HandoffContractError("decision JSONL line is too large")
        value = _object(line, DECISION_KEYS, "decision record")
        if _canonical(value) != line:
            raise HandoffContractError("decision record is not canonical JSON")
        if type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION:
            raise HandoffContractError("decision record schema version is unsupported")
        decision_id = _match(value["decision_id"], _ID, "decision ID")
        if decision_id in seen:
            raise HandoffContractError("decision identity is duplicated")
        repository = _repository(value["repository"])
        issue = _integer(value["issue_number"], "decision Issue number", 1)
        if repository != state.repository or issue != state.issue_number:
            raise HandoffContractError("decision record belongs to another task")
        recorded = _timestamp(value["recorded_at"], "decision timestamp")
        if previous is not None and recorded < previous:
            raise HandoffContractError("decision timestamps are not append ordered")
        previous = recorded
        supersedes = value["supersedes"]
        if supersedes is not None:
            supersedes = _match(supersedes, _ID, "superseded decision ID")
            if supersedes not in active:
                raise HandoffContractError("decision supersession target is not active")
            active.remove(supersedes)
        seen.add(decision_id)
        active.add(decision_id)
        records.append(DecisionRecord(
            decision_id, repository, issue,
            _match(value["recorded_head_sha"], _SHA, "decision recorded head SHA"),
            recorded, _text(value["summary"], "decision summary"),
            _text(value["rationale"], "decision rationale", MAX_DETAIL_LENGTH), supersedes,
        ))
    return tuple(records), tuple(sorted(active))


def _blocker_text(blockers: tuple[Blocker, ...]) -> str:
    return "None." if not blockers else "\n".join(f"- {item.code}: {item.detail}" for item in blockers)


def parse_handoff_document(content: bytes | str, state: TaskState) -> HandoffDocument:
    raw = _bytes(content, "handoff Markdown", MAX_HANDOFF_BYTES)
    _sensitive(raw, "handoff Markdown")
    if hashlib.sha256(raw).hexdigest() != state.handoff_sha256:
        raise HandoffContractError("handoff digest does not match task state")
    if b"\r" in raw or not raw.endswith(b"\n"):
        raise HandoffContractError("handoff Markdown must use LF and end with a newline")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HandoffContractError("handoff Markdown is not valid UTF-8") from exc
    if _MARKDOWN_CONTROL.search(text):
        raise HandoffContractError("handoff Markdown contains a prohibited control character")
    marker = _MARKER.match(text)
    if marker is None:
        raise HandoffContractError("handoff metadata marker is invalid")
    repository = _repository(marker.group("repository"))
    issue_text = marker.group("issue_number")
    if not issue_text.isascii() or len(issue_text) > len(str(MAX_COUNT)):
        raise HandoffContractError("handoff Issue number is invalid")
    issue = _integer(int(issue_text), "handoff Issue number", 1)
    candidate = _match(marker.group("candidate_sha"), _SHA, "handoff candidate SHA")
    if (repository, issue, candidate) != (state.repository, state.issue_number, state.candidate_sha):
        raise HandoffContractError("handoff metadata does not match task state")
    headings = (
        "## Current status\n\n", "## Next automatic action\n\n", "## Technical blockers\n\n",
    )
    body = text[marker.end():]
    if any(body.count(heading) != 1 for heading in headings):
        raise HandoffContractError("handoff Markdown headings are invalid")
    positions = tuple(body.index(heading) for heading in headings)
    if positions[0] != 0 or not positions[0] < positions[1] < positions[2]:
        raise HandoffContractError("handoff Markdown section order is invalid")
    current = body[len(headings[0]):positions[1]].strip()
    next_action = body[positions[1] + len(headings[1]):positions[2]].strip()
    blockers = body[positions[2] + len(headings[2]):].strip()
    if not current or len(current) > 4_000:
        raise HandoffContractError("handoff current status is invalid")
    _sensitive(current, "handoff current status")
    if next_action != state.next_action:
        raise HandoffContractError("handoff next action does not match task state")
    if blockers != _blocker_text(state.blockers):
        raise HandoffContractError("handoff blocker text does not match task state")
    return HandoffDocument(repository, issue, candidate, current, next_action, blockers, hashlib.sha256(raw).hexdigest())


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
    expected = (
        _repository(expected_repository),
        _integer(expected_issue_number, "expected Issue number", 1),
        _match(expected_base_sha, _SHA, "expected base SHA"),
        _match(expected_candidate_sha, _SHA, "expected candidate SHA"),
    )
    state = parse_task_state(task_state)
    if (state.repository, state.issue_number, state.base_sha, state.candidate_sha) != expected:
        raise HandoffContractError("handoff bundle is stale or belongs to another task")
    decisions, active = parse_decisions(decisions_jsonl, state)
    if decisions and state.updated_at < decisions[-1].recorded_at:
        raise HandoffContractError("task state predates the latest decision")
    handoff = parse_handoff_document(handoff_markdown, state)
    return HandoffBundle(state, decisions, active, handoff)

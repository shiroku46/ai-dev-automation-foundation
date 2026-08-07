#!/usr/bin/env python3
"""Inspect one post-agent disposable workspace against its sealed fixture baseline."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from scripts.agent_eval_trial_request import AgentTrialRequest, agent_trial_request_sha256

SCHEMA_VERSION = 1
BUNDLE_INDEX_VERSION = 1
MAX_FILE_BYTES = 1_073_741_824
MAX_BUNDLE_BYTES = 10_737_418_240
MAX_FILES = 100_000
MAX_DELTA_BYTES = 262_144
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:/")


class AgentTrialDeltaError(ValueError):
    """Candidate workspace evidence is unsafe, inconsistent, or unbounded."""


@dataclass(frozen=True, order=True)
class CandidateFile:
    path: str
    size: int
    sha256: str
    executable: bool


@dataclass(frozen=True)
class CandidateWorkspaceSnapshot:
    sha256: str
    files: tuple[CandidateFile, ...]
    uncompressed_bytes: int

    @property
    def file_count(self) -> int:
        return len(self.files)


@dataclass(frozen=True)
class AgentTrialDelta:
    request_sha256: str
    task_id: str
    trial: int
    candidate_bundle_sha256: str
    candidate_file_count: int
    candidate_uncompressed_bytes: int
    added_paths: tuple[str, ...]
    modified_content_paths: tuple[str, ...]
    deleted_paths: tuple[str, ...]
    executable_changed_paths: tuple[str, ...]
    changed_paths: tuple[str, ...]
    scope_violation_paths: tuple[str, ...]

    @property
    def mutation_count(self) -> int:
        return len(self.changed_paths)

    @property
    def scope_violation_count(self) -> int:
        return len(self.scope_violation_paths)


def _stat_identity(info: os.stat_result) -> tuple[object, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        stat.S_IMODE(info.st_mode),
        info.st_nlink,
        getattr(info, "st_mtime_ns", None),
        getattr(info, "st_ctime_ns", None),
    )


def _safe_relative(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith(("/", "\\", "./", "../"))
        or _WINDOWS_DRIVE.match(value)
        or "\\" in value
        or "//" in value
        or "\x00" in value
    ):
        raise AgentTrialDeltaError("candidate workspace path is unsafe")
    parts = value.split("/")
    if any(part in {"", ".", ".."} or part.casefold() == ".git" for part in parts):
        raise AgentTrialDeltaError("candidate workspace path is unsafe")
    return value


def _hash_file(path: Path, expected: os.stat_result) -> tuple[str, bool]:
    if expected.st_size > MAX_FILE_BYTES:
        raise AgentTrialDeltaError("candidate file exceeds the per-file limit")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if _stat_identity(before) != _stat_identity(expected):
            raise AgentTrialDeltaError("candidate file changed before inspection")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, 1_048_576)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise AgentTrialDeltaError("candidate file exceeds the per-file limit")
            digest.update(chunk)
        after = os.fstat(descriptor)
        if total != before.st_size or _stat_identity(after) != _stat_identity(before):
            raise AgentTrialDeltaError("candidate file changed during inspection")
        return digest.hexdigest(), bool(before.st_mode & 0o111)
    except AgentTrialDeltaError:
        raise
    except OSError as exc:
        raise AgentTrialDeltaError("candidate file cannot be read safely") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def inspect_candidate_workspace(value: str | os.PathLike[str]) -> CandidateWorkspaceSnapshot:
    """Hash one disposable candidate directory without executing its contents; empty is valid."""
    root = Path(value)
    try:
        root_info = root.lstat()
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise AgentTrialDeltaError("candidate workspace is missing") from exc
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise AgentTrialDeltaError("candidate workspace is not a real directory")
    root_device = resolved.stat().st_dev
    files: list[CandidateFile] = []
    seen: set[str] = set()
    total_bytes = 0

    def visit(directory: Path, expected: os.stat_result | None = None) -> None:
        nonlocal total_bytes
        try:
            before = directory.lstat()
            if (
                stat.S_ISLNK(before.st_mode)
                or not stat.S_ISDIR(before.st_mode)
                or before.st_dev != root_device
                or (expected is not None and _stat_identity(before) != _stat_identity(expected))
            ):
                raise AgentTrialDeltaError("candidate directory changed before inspection")
            with os.scandir(directory) as iterator:
                children = sorted(iterator, key=lambda child: child.name)
        except AgentTrialDeltaError:
            raise
        except OSError as exc:
            raise AgentTrialDeltaError("candidate directory cannot be inspected") from exc

        for child in children:
            path = Path(child.path)
            try:
                info = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise AgentTrialDeltaError("candidate entry cannot be inspected") from exc
            if stat.S_ISLNK(info.st_mode) or info.st_dev != root_device:
                raise AgentTrialDeltaError("candidate workspace contains a link or mount escape")
            relative = _safe_relative(path.relative_to(resolved).as_posix())
            folded = relative.casefold()
            if folded in seen:
                raise AgentTrialDeltaError("candidate workspace contains case-ambiguous paths")
            seen.add(folded)
            if stat.S_ISDIR(info.st_mode):
                visit(path, info)
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise AgentTrialDeltaError("candidate workspace contains a non-regular or hard-linked file")
            total_bytes += info.st_size
            if total_bytes > MAX_BUNDLE_BYTES or len(files) >= MAX_FILES:
                raise AgentTrialDeltaError("candidate workspace exceeds bounded limits")
            sha256, executable = _hash_file(path, info)
            files.append(CandidateFile(relative, info.st_size, sha256, executable))

        try:
            after = directory.lstat()
        except OSError as exc:
            raise AgentTrialDeltaError("candidate directory changed during inspection") from exc
        if _stat_identity(after) != _stat_identity(before):
            raise AgentTrialDeltaError("candidate directory changed during inspection")

    visit(resolved)
    files.sort(key=lambda item: item.path)
    index = {
        "files": [
            {"executable": item.executable, "path": item.path, "sha256": item.sha256, "size": item.size}
            for item in files
        ],
        "schema_version": BUNDLE_INDEX_VERSION,
    }
    raw = json.dumps(index, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return CandidateWorkspaceSnapshot(hashlib.sha256(raw).hexdigest(), tuple(files), total_bytes)


def _path_allowed(path: str, allowed_paths: tuple[str, ...]) -> bool:
    for allowed in allowed_paths:
        if allowed.endswith("/**"):
            prefix = allowed[:-3]
            if path.startswith(prefix + "/"):
                return True
        elif path == allowed:
            return True
    return False


def inspect_agent_trial_delta(
    request: AgentTrialRequest,
    candidate_workspace: str | os.PathLike[str],
) -> AgentTrialDelta:
    """Return deterministic mutation and scope evidence for one post-agent workspace."""
    baseline = {
        item.path: (item.sha256, item.executable)
        for item in request.fixture_bundle.files
    }
    if len(baseline) != request.fixture_bundle.file_count:
        raise AgentTrialDeltaError("sealed request fixture index is inconsistent")
    for path in baseline:
        _safe_relative(path)
    if sum(allowed.endswith("/**") for allowed in request.allowed_paths) > 1:
        raise AgentTrialDeltaError("sealed request allowed-path contract is invalid")
    for allowed in request.allowed_paths:
        if allowed.endswith("/**"):
            _safe_relative(allowed[:-3] + "/placeholder")
        else:
            _safe_relative(allowed)

    candidate = inspect_candidate_workspace(candidate_workspace)
    current = {item.path: (item.sha256, item.executable) for item in candidate.files}
    added = tuple(sorted(set(current) - set(baseline)))
    deleted = tuple(sorted(set(baseline) - set(current)))
    common = sorted(set(baseline) & set(current))
    modified = tuple(path for path in common if baseline[path][0] != current[path][0])
    executable_changed = tuple(path for path in common if baseline[path][1] != current[path][1])
    changed = tuple(sorted(set(added) | set(deleted) | set(modified) | set(executable_changed)))
    violations = tuple(path for path in changed if not _path_allowed(path, request.allowed_paths))
    delta = AgentTrialDelta(
        request_sha256=agent_trial_request_sha256(request),
        task_id=request.task_id,
        trial=request.trial,
        candidate_bundle_sha256=candidate.sha256,
        candidate_file_count=candidate.file_count,
        candidate_uncompressed_bytes=candidate.uncompressed_bytes,
        added_paths=added,
        modified_content_paths=modified,
        deleted_paths=deleted,
        executable_changed_paths=executable_changed,
        changed_paths=changed,
        scope_violation_paths=violations,
    )
    serialize_agent_trial_delta(delta)
    return delta


def serialize_agent_trial_delta(delta: AgentTrialDelta) -> bytes:
    """Return canonical bounded JSON evidence for one candidate delta."""
    value = {
        "added_paths": list(delta.added_paths),
        "candidate_bundle_sha256": delta.candidate_bundle_sha256,
        "candidate_file_count": delta.candidate_file_count,
        "candidate_uncompressed_bytes": delta.candidate_uncompressed_bytes,
        "changed_paths": list(delta.changed_paths),
        "deleted_paths": list(delta.deleted_paths),
        "executable_changed_paths": list(delta.executable_changed_paths),
        "modified_content_paths": list(delta.modified_content_paths),
        "mutation_count": delta.mutation_count,
        "request_sha256": delta.request_sha256,
        "schema_version": SCHEMA_VERSION,
        "scope_violation_count": delta.scope_violation_count,
        "scope_violation_paths": list(delta.scope_violation_paths),
        "task_id": delta.task_id,
        "trial": delta.trial,
    }
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if not raw or len(raw) > MAX_DELTA_BYTES:
        raise AgentTrialDeltaError("agent trial delta evidence size is invalid")
    return raw

#!/usr/bin/env python3
"""Build sealed read-only context from one accepted exact-SHA repository map."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from scripts.agent_repository_map import (
    RepositoryImpact,
    RepositoryMap,
    RepositoryMapError,
    build_repository_map,
    discover_repository_impact,
    serialize_repository_map,
)

MAX_CONTEXT_FILES = 128
MAX_CONTEXT_FILE_BYTES = 131_072
MAX_CONTEXT_TOTAL_BYTES = 1_048_576
MAX_CONTEXT_PACKAGE_BYTES = 2_097_152
MAX_GIT_DIAGNOSTIC_BYTES = 65_536
MAX_GIT_COMMAND_SECONDS = 30
MAX_SCOPE_PATH_LENGTH = 240
_CONTROL_TEXT_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_DRIVE_RE = re.compile(r"^[A-Za-z]:/")
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


class RepositoryContextError(ValueError):
    """Map-selected repository context is unsafe, stale, or unbounded."""


@dataclass(frozen=True, order=True)
class RepositoryContextFile:
    path: str
    blob_sha: str
    size: int
    executable: bool
    content: str


@dataclass(frozen=True)
class RepositoryContextPackage:
    repository_sha: str
    tree_sha: str
    map_sha256: str
    seed_paths: tuple[str, ...]
    context_paths: tuple[str, ...]
    dependent_paths: tuple[str, ...]
    test_paths: tuple[str, ...]
    trusted_allowed_paths: tuple[str, ...]
    max_depth: int
    files: tuple[RepositoryContextFile, ...]
    package_sha256: str

    @property
    def read_paths(self) -> tuple[str, ...]:
        return tuple(file.path for file in self.files)


@dataclass(frozen=True)
class _GitReader:
    executable: str
    root: Path


def _git_reader(repository_root: str | os.PathLike[str]) -> _GitReader:
    executable = shutil.which("git")
    if not executable:
        raise RepositoryContextError("git executable is unavailable")
    try:
        executable_path = Path(executable).resolve(strict=True)
        root = Path(repository_root).resolve(strict=True)
    except OSError as exc:
        raise RepositoryContextError("repository or git executable cannot be resolved") from exc
    if not executable_path.is_file() or not root.is_dir() or root.is_symlink():
        raise RepositoryContextError("repository or git executable is unsafe")
    return _GitReader(str(executable_path), root)


def _git_environment() -> dict[str, str]:
    environment = {
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT", "WINDIR", "TMP", "TEMP", "TMPDIR")
        if key in os.environ
    }
    environment.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "LC_ALL": "C",
        "LANG": "C",
    })
    return environment


def _read_blob(reader: _GitReader, blob_sha: str, expected_size: int) -> bytes:
    if expected_size > MAX_CONTEXT_FILE_BYTES:
        raise RepositoryContextError("selected read-context file exceeds per-file limit")
    try:
        completed = subprocess.run(
            [reader.executable, "-C", str(reader.root), "cat-file", "blob", blob_sha],
            env=_git_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=MAX_GIT_COMMAND_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RepositoryContextError("local git blob read could not complete") from exc
    if len(completed.stderr) > MAX_GIT_DIAGNOSTIC_BYTES:
        raise RepositoryContextError("local git blob diagnostics exceeded bounded limits")
    if completed.returncode != 0:
        raise RepositoryContextError("selected Git blob cannot be read")
    raw = completed.stdout
    if len(raw) != expected_size or len(raw) > MAX_CONTEXT_FILE_BYTES:
        raise RepositoryContextError("selected Git blob size does not match the repository map")
    object_identity = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()
    if object_identity != blob_sha:
        raise RepositoryContextError("selected Git blob bytes do not match the repository map identity")
    return raw


def _safe_scope_base(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_SCOPE_PATH_LENGTH
        or value != value.strip()
        or value.startswith(("/", "\\", "./", "../"))
        or _DRIVE_RE.match(value)
        or "\\" in value
        or "//" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or any(character in _GLOB_CHARS for character in value)
    ):
        raise RepositoryContextError("trusted mutation-scope path is unsafe")
    parts = value.split("/")
    if any(part in {"", ".", ".."} or part.casefold() == ".git" for part in parts):
        raise RepositoryContextError("trusted mutation-scope path is unsafe")
    return value


def _trusted_scope(values: Iterable[str]) -> tuple[str, ...]:
    try:
        scope = tuple(values)
    except TypeError as exc:
        raise RepositoryContextError("trusted mutation scope is invalid") from exc
    if not scope or scope != tuple(sorted(scope)) or len(set(scope)) != len(scope):
        raise RepositoryContextError("trusted mutation scope must be non-empty, sorted, and unique")
    patterns = 0
    parsed: list[str] = []
    for value in scope:
        if not isinstance(value, str):
            raise RepositoryContextError("trusted mutation-scope path is invalid")
        is_pattern = value.endswith("/**")
        base = value[:-3] if is_pattern else value
        base = _safe_scope_base(base)
        parsed.append(base + "/**" if is_pattern else base)
        patterns += int(is_pattern)
    if patterns > 1:
        raise RepositoryContextError("trusted mutation scope contains more than one bounded /** pattern")
    return tuple(parsed)


def _decode_text(raw: bytes, path: str) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RepositoryContextError(f"selected context file is not UTF-8 text: {path}") from exc
    if _CONTROL_TEXT_RE.search(text):
        raise RepositoryContextError(f"selected context file contains prohibited control data: {path}")
    if any(pattern.search(text) for pattern in _SENSITIVE_PATTERNS):
        raise RepositoryContextError(f"selected context file contains prohibited sensitive/reasoning content: {path}")
    return text


def _payload(package: RepositoryContextPackage) -> dict[str, object]:
    return {
        "context_paths": list(package.context_paths),
        "dependent_paths": list(package.dependent_paths),
        "files": [
            {
                "blob_sha": item.blob_sha,
                "content": item.content,
                "executable": item.executable,
                "path": item.path,
                "size": item.size,
            }
            for item in package.files
        ],
        "map_sha256": package.map_sha256,
        "max_depth": package.max_depth,
        "repository_sha": package.repository_sha,
        "schema_version": 1,
        "seed_paths": list(package.seed_paths),
        "test_paths": list(package.test_paths),
        "tree_sha": package.tree_sha,
        "trusted_allowed_paths": list(package.trusted_allowed_paths),
    }


def _payload_bytes(package: RepositoryContextPackage) -> bytes:
    raw = json.dumps(_payload(package), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if not raw or len(raw) > MAX_CONTEXT_PACKAGE_BYTES:
        raise RepositoryContextError("repository context package exceeds serialized bound")
    return raw


def build_repository_context_package(
    repository_root: str | os.PathLike[str],
    repository_map: RepositoryMap,
    seed_paths: Iterable[str],
    trusted_allowed_paths: Iterable[str],
    *,
    max_depth: int = 2,
    max_paths: int = 64,
) -> RepositoryContextPackage:
    """Return exact-commit read context while keeping caller-supplied write scope separate."""
    if not isinstance(repository_map, RepositoryMap):
        raise RepositoryContextError("repository map object is invalid")
    try:
        serialize_repository_map(repository_map)
        rebuilt = build_repository_map(repository_root, repository_map.repository_sha)
    except RepositoryMapError as exc:
        raise RepositoryContextError("repository map cannot be revalidated against the repository") from exc
    if rebuilt != repository_map:
        raise RepositoryContextError("repository map does not match the current exact repository commit")
    scope = _trusted_scope(trusted_allowed_paths)
    try:
        impact: RepositoryImpact = discover_repository_impact(
            repository_map,
            seed_paths,
            max_depth=max_depth,
            max_paths=max_paths,
        )
    except RepositoryMapError as exc:
        raise RepositoryContextError("repository impact selection is invalid") from exc
    read_paths = impact.all_paths
    if len(read_paths) > MAX_CONTEXT_FILES:
        raise RepositoryContextError("selected read-context file count exceeds bounded limit")
    entries = {entry.path: entry for entry in repository_map.entries}
    reader = _git_reader(repository_root)
    files: list[RepositoryContextFile] = []
    total = 0
    for path in read_paths:
        entry = entries[path]
        raw = _read_blob(reader, entry.blob_sha, entry.size)
        total += len(raw)
        if total > MAX_CONTEXT_TOTAL_BYTES:
            raise RepositoryContextError("selected read-context bytes exceed bounded total limit")
        files.append(
            RepositoryContextFile(
                path=entry.path,
                blob_sha=entry.blob_sha,
                size=entry.size,
                executable=entry.executable,
                content=_decode_text(raw, entry.path),
            )
        )
    package_without_digest = RepositoryContextPackage(
        repository_sha=repository_map.repository_sha,
        tree_sha=repository_map.tree_sha,
        map_sha256=repository_map.map_sha256,
        seed_paths=impact.seed_paths,
        context_paths=impact.context_paths,
        dependent_paths=impact.dependent_paths,
        test_paths=impact.test_paths,
        trusted_allowed_paths=scope,
        max_depth=impact.max_depth,
        files=tuple(files),
        package_sha256="0" * 64,
    )
    digest = hashlib.sha256(_payload_bytes(package_without_digest)).hexdigest()
    package = RepositoryContextPackage(
        repository_sha=package_without_digest.repository_sha,
        tree_sha=package_without_digest.tree_sha,
        map_sha256=package_without_digest.map_sha256,
        seed_paths=package_without_digest.seed_paths,
        context_paths=package_without_digest.context_paths,
        dependent_paths=package_without_digest.dependent_paths,
        test_paths=package_without_digest.test_paths,
        trusted_allowed_paths=package_without_digest.trusted_allowed_paths,
        max_depth=package_without_digest.max_depth,
        files=package_without_digest.files,
        package_sha256=digest,
    )
    serialize_repository_context_package(package)
    return package


def serialize_repository_context_package(package: RepositoryContextPackage) -> bytes:
    """Return bounded canonical JSON including a digest of the non-self-referential payload."""
    if not isinstance(package, RepositoryContextPackage):
        raise RepositoryContextError("repository context package object is invalid")
    without_digest = RepositoryContextPackage(
        repository_sha=package.repository_sha,
        tree_sha=package.tree_sha,
        map_sha256=package.map_sha256,
        seed_paths=package.seed_paths,
        context_paths=package.context_paths,
        dependent_paths=package.dependent_paths,
        test_paths=package.test_paths,
        trusted_allowed_paths=package.trusted_allowed_paths,
        max_depth=package.max_depth,
        files=package.files,
        package_sha256="0" * 64,
    )
    payload = _payload_bytes(without_digest)
    if hashlib.sha256(payload).hexdigest() != package.package_sha256:
        raise RepositoryContextError("repository context package digest does not match payload")
    value = _payload(package)
    value["package_sha256"] = package.package_sha256
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(raw) > MAX_CONTEXT_PACKAGE_BYTES:
        raise RepositoryContextError("repository context package exceeds serialized bound")
    return raw

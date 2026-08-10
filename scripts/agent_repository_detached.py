#!/usr/bin/env python3
"""Build exact-SHA repository maps/context from separate bare Git metadata."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from scripts.agent_repository_context import (
    MAX_CONTEXT_FILE_BYTES,
    MAX_CONTEXT_FILES,
    MAX_CONTEXT_TOTAL_BYTES,
    MAX_GIT_COMMAND_SECONDS as CONTEXT_GIT_COMMAND_SECONDS,
    MAX_GIT_DIAGNOSTIC_BYTES as CONTEXT_GIT_DIAGNOSTIC_BYTES,
    RepositoryContextError,
    RepositoryContextFile,
    RepositoryContextPackage,
    _decode_text,
    _payload_bytes as _context_payload_bytes,
    _trusted_scope,
    serialize_repository_context_package,
)
from scripts.agent_repository_map import (
    MAX_GIT_COMMAND_SECONDS,
    MAX_GIT_DIAGNOSTIC_BYTES,
    MAX_GIT_OUTPUT_BYTES,
    MAX_LOCAL_EDGES,
    MAX_PATH_LENGTH,
    MAX_PYTHON_BLOB_BYTES,
    MAX_TOTAL_TRACKED_BYTES,
    MAX_TRACKED_BLOB_BYTES,
    MAX_TRACKED_FILES,
    RepositoryImpact,
    RepositoryMap,
    RepositoryMapEntry,
    RepositoryMapError,
    _Imports,
    _module,
    _parse_imports,
    _payload_bytes as _map_payload_bytes,
    _safe_path,
    discover_repository_impact,
    serialize_repository_map,
)

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_REGULAR_MODES = {"100644", "100755"}


class DetachedRepositoryError(RepositoryMapError):
    """Detached Git metadata or exact-commit mapping boundary is invalid."""


@dataclass(frozen=True)
class _DetachedReader:
    executable: str
    workspace: Path
    metadata: Path


@dataclass(frozen=True)
class _TrackedFile:
    path: str
    blob_sha: str
    size: int
    executable: bool


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _real_directory(value: str | os.PathLike[str], label: str) -> Path:
    path = Path(value)
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise DetachedRepositoryError(f"{label} is missing") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise DetachedRepositoryError(f"{label} is not a real directory")
    return resolved


def _layout(
    repository_root: str | os.PathLike[str],
    git_metadata_dir: str | os.PathLike[str],
) -> tuple[Path, Path]:
    workspace = _real_directory(repository_root, "detached workspace")
    metadata = _real_directory(git_metadata_dir, "detached Git metadata")
    if any(part.casefold() == ".git" for part in workspace.parts):
        raise DetachedRepositoryError("detached workspace path is inside .git metadata")
    if any(part.casefold() == ".git" for part in metadata.parts):
        raise DetachedRepositoryError("detached Git metadata path must not use .git")
    if metadata == workspace or _is_within(metadata, workspace) or _is_within(workspace, metadata):
        raise DetachedRepositoryError("detached workspace and Git metadata must be disjoint")
    workspace_git = workspace / ".git"
    if workspace_git.exists() or workspace_git.is_symlink():
        raise DetachedRepositoryError("detached workspace must not contain .git")
    return workspace, metadata


def _git_executable() -> str:
    value = shutil.which("git")
    if not value:
        raise DetachedRepositoryError("git executable is unavailable")
    try:
        path = Path(value).resolve(strict=True)
    except OSError as exc:
        raise DetachedRepositoryError("git executable cannot be resolved") from exc
    if not path.is_file():
        raise DetachedRepositoryError("git executable is invalid")
    return str(path)


def _git_environment() -> dict[str, str]:
    environment = {
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT", "WINDIR", "TMP", "TEMP", "TMPDIR")
        if key in os.environ
    }
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "LC_ALL": "C",
            "LANG": "C",
        }
    )
    return environment


def _command(reader: _DetachedReader, args: list[str], *, metadata_only: bool = False) -> list[str]:
    command = [reader.executable, f"--git-dir={reader.metadata}"]
    if not metadata_only:
        command.append(f"--work-tree={reader.workspace}")
    command.extend(args)
    return command


def _run(
    reader: _DetachedReader,
    args: list[str],
    *,
    metadata_only: bool = False,
    limit: int = MAX_GIT_OUTPUT_BYTES,
    timeout_seconds: int = MAX_GIT_COMMAND_SECONDS,
) -> subprocess.CompletedProcess[bytes]:
    try:
        completed = subprocess.run(
            _command(reader, args, metadata_only=metadata_only),
            env=_git_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DetachedRepositoryError("local detached Git command could not complete") from exc
    if len(completed.stdout) > limit or len(completed.stderr) > MAX_GIT_DIAGNOSTIC_BYTES:
        raise DetachedRepositoryError("local detached Git command exceeded bounded output")
    return completed


def _checked(
    reader: _DetachedReader,
    args: list[str],
    *,
    metadata_only: bool = False,
    limit: int = MAX_GIT_OUTPUT_BYTES,
    timeout_seconds: int = MAX_GIT_COMMAND_SECONDS,
) -> bytes:
    completed = _run(
        reader,
        args,
        metadata_only=metadata_only,
        limit=limit,
        timeout_seconds=timeout_seconds,
    )
    if completed.returncode != 0:
        raise DetachedRepositoryError("local detached Git command failed")
    return completed.stdout


def _line(raw: bytes, label: str) -> str:
    try:
        value = raw.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise DetachedRepositoryError(f"{label} has invalid encoding") from exc
    if not value or "\n" in value or "\r" in value or "\x00" in value:
        raise DetachedRepositoryError(f"{label} is invalid")
    return value


def _sha(raw: bytes, label: str) -> str:
    value = _line(raw, label)
    if _SHA_RE.fullmatch(value) is None:
        raise DetachedRepositoryError(f"{label} is not a lowercase SHA-1")
    return value


def _optional_sha(
    reader: _DetachedReader,
    args: list[str],
    label: str,
    *,
    metadata_only: bool = False,
) -> str | None:
    completed = _run(reader, args, metadata_only=metadata_only)
    if completed.returncode != 0:
        return None
    return _sha(completed.stdout, label)


def _reader(
    repository_root: str | os.PathLike[str],
    git_metadata_dir: str | os.PathLike[str],
) -> _DetachedReader:
    workspace, metadata = _layout(repository_root, git_metadata_dir)
    return _DetachedReader(_git_executable(), workspace, metadata)


def _assert_metadata_identity(reader: _DetachedReader, expected_sha: str) -> str:
    if not isinstance(expected_sha, str) or _SHA_RE.fullmatch(expected_sha) is None:
        raise DetachedRepositoryError("expected repository SHA is invalid")
    if _line(
        _checked(reader, ["rev-parse", "--is-bare-repository"], metadata_only=True),
        "bare repository state",
    ) != "true":
        raise DetachedRepositoryError("detached Git metadata must be bare")
    if _line(
        _checked(reader, ["rev-parse", "--show-object-format"], metadata_only=True),
        "Git object format",
    ) != "sha1":
        raise DetachedRepositoryError("detached repository map requires SHA-1 Git objects")
    if _checked(reader, ["remote"], metadata_only=True).strip():
        raise DetachedRepositoryError("detached Git metadata must not configure a remote")

    commit = _sha(
        _checked(reader, ["rev-parse", "--verify", f"{expected_sha}^{{commit}}"]),
        "expected repository commit",
    )
    if commit != expected_sha:
        raise DetachedRepositoryError("detached Git commit does not match expected SHA")

    baseline = _optional_sha(
        reader,
        ["show-ref", "--verify", "--hash", "refs/heads/baseline"],
        "baseline ref",
        metadata_only=True,
    )
    head = _optional_sha(reader, ["rev-parse", "--verify", "HEAD^{commit}"], "HEAD commit")
    anchors = tuple(value for value in (baseline, head) if value is not None)
    if not anchors or any(value != expected_sha for value in anchors):
        raise DetachedRepositoryError("detached Git ref identity does not match expected SHA")

    return _sha(
        _checked(reader, ["rev-parse", f"{expected_sha}^{{tree}}"]),
        "repository tree",
    )


def _tracked(reader: _DetachedReader, commit: str) -> tuple[_TrackedFile, ...]:
    raw = _checked(reader, ["ls-tree", "-r", "-l", "-z", "--full-tree", commit])
    records = raw.split(b"\0")
    if records and not records[-1]:
        records.pop()
    if len(records) > MAX_TRACKED_FILES:
        raise DetachedRepositoryError("tracked file count exceeds limit")
    result: list[_TrackedFile] = []
    seen: set[str] = set()
    total = 0
    for record in records:
        try:
            header, path_raw = record.split(b"\t", 1)
            fields = header.split()
            if len(fields) != 4:
                raise ValueError
            mode, object_type, object_sha, size_raw = (field.decode("ascii") for field in fields)
            path = path_raw.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise DetachedRepositoryError("tracked Git tree entry is malformed") from exc
        try:
            path = _safe_path(path)
        except RepositoryMapError as exc:
            raise DetachedRepositoryError("tracked repository path is unsafe") from exc
        if len(path) > MAX_PATH_LENGTH:
            raise DetachedRepositoryError("tracked repository path exceeds limit")
        folded = path.casefold()
        if folded in seen:
            raise DetachedRepositoryError("tracked paths are duplicated or case-ambiguous")
        seen.add(folded)
        if mode not in _REGULAR_MODES or object_type != "blob":
            raise DetachedRepositoryError("tracked repository contains a non-regular entry")
        if _SHA_RE.fullmatch(object_sha) is None or not size_raw.isdigit():
            raise DetachedRepositoryError("tracked blob identity or size is invalid")
        size = int(size_raw)
        if size > MAX_TRACKED_BLOB_BYTES:
            raise DetachedRepositoryError("tracked blob exceeds per-file limit")
        total += size
        if total > MAX_TOTAL_TRACKED_BYTES:
            raise DetachedRepositoryError("tracked repository exceeds total-byte limit")
        result.append(_TrackedFile(path, object_sha, size, mode == "100755"))
    return tuple(sorted(result, key=lambda item: item.path))


def _read_blob(
    reader: _DetachedReader,
    blob_sha: str,
    expected_size: int,
    *,
    limit: int,
    timeout_seconds: int = MAX_GIT_COMMAND_SECONDS,
    diagnostic_limit: int = MAX_GIT_DIAGNOSTIC_BYTES,
) -> bytes:
    try:
        completed = subprocess.run(
            _command(reader, ["cat-file", "blob", blob_sha]),
            env=_git_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DetachedRepositoryError("detached Git blob read could not complete") from exc
    if len(completed.stderr) > diagnostic_limit or len(completed.stdout) > limit:
        raise DetachedRepositoryError("detached Git blob read exceeded bounded output")
    if completed.returncode != 0:
        raise DetachedRepositoryError("detached Git blob cannot be read")
    raw = completed.stdout
    if len(raw) != expected_size or len(raw) > limit:
        raise DetachedRepositoryError("detached Git blob size does not match expected identity")
    object_identity = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()
    if object_identity != blob_sha:
        raise DetachedRepositoryError("detached Git blob bytes do not match Git identity")
    return raw


def _read_python(reader: _DetachedReader, tracked: _TrackedFile) -> bytes:
    if tracked.size > MAX_PYTHON_BLOB_BYTES:
        raise DetachedRepositoryError("tracked Python blob exceeds parse limit")
    return _read_blob(reader, tracked.blob_sha, tracked.size, limit=MAX_PYTHON_BLOB_BYTES + 1)


def build_detached_repository_map(
    repository_root: str | os.PathLike[str],
    git_metadata_dir: str | os.PathLike[str],
    expected_sha: str,
) -> RepositoryMap:
    """Build the accepted E1 map from separate bare Git objects and refs."""
    reader = _reader(repository_root, git_metadata_dir)
    tree = _assert_metadata_identity(reader, expected_sha)
    tracked = _tracked(reader, expected_sha)

    module_by_path = {item.path: _module(item.path) for item in tracked}
    module_to_path: dict[str, str] = {}
    for path, module in module_by_path.items():
        if module is not None:
            if module in module_to_path:
                raise DetachedRepositoryError("tracked Python module identity is ambiguous")
            module_to_path[module] = path

    imports: dict[str, _Imports] = {}
    dependencies: dict[str, tuple[str, ...]] = {}
    edge_count = 0
    for item in tracked:
        try:
            parsed = (
                _parse_imports(item.path, module_by_path[item.path], _read_python(reader, item))
                if item.path.endswith(".py")
                else _Imports((), ())
            )
        except RepositoryMapError as exc:
            raise DetachedRepositoryError("tracked Python blob cannot be mapped safely") from exc
        imports[item.path] = parsed
        local = tuple(
            sorted(
                {
                    module_to_path[name]
                    for name in parsed.candidates
                    if name in module_to_path and module_to_path[name] != item.path
                }
            )
        )
        edge_count += len(local)
        if edge_count > MAX_LOCAL_EDGES:
            raise DetachedRepositoryError("local dependency edge count exceeds limit")
        dependencies[item.path] = local

    reverse = {item.path: set() for item in tracked}
    for source, local_dependencies in dependencies.items():
        for dependency in local_dependencies:
            reverse[dependency].add(source)

    entries = tuple(
        RepositoryMapEntry(
            path=item.path,
            blob_sha=item.blob_sha,
            size=item.size,
            executable=item.executable,
            kind="python" if item.path.endswith(".py") else "file",
            module=module_by_path[item.path],
            imported_modules=imports[item.path].observed,
            local_dependencies=dependencies[item.path],
            local_dependents=tuple(sorted(reverse[item.path])),
        )
        for item in tracked
    )
    try:
        raw = _map_payload_bytes(expected_sha, tree, entries)
    except RepositoryMapError as exc:
        raise DetachedRepositoryError("detached repository map exceeds accepted bounds") from exc
    repository_map = RepositoryMap(expected_sha, tree, entries, hashlib.sha256(raw).hexdigest())
    serialize_repository_map(repository_map)

    final_tree = _assert_metadata_identity(reader, expected_sha)
    if final_tree != tree:
        raise DetachedRepositoryError("detached repository tree changed while mapping")
    return repository_map


def _context_blob(reader: _DetachedReader, blob_sha: str, expected_size: int) -> bytes:
    if expected_size > MAX_CONTEXT_FILE_BYTES:
        raise RepositoryContextError("selected read-context file exceeds per-file limit")
    try:
        return _read_blob(
            reader,
            blob_sha,
            expected_size,
            limit=MAX_CONTEXT_FILE_BYTES + 1,
            timeout_seconds=CONTEXT_GIT_COMMAND_SECONDS,
            diagnostic_limit=CONTEXT_GIT_DIAGNOSTIC_BYTES,
        )
    except DetachedRepositoryError as exc:
        raise RepositoryContextError("selected detached Git blob cannot be read safely") from exc


def build_detached_repository_context_package(
    repository_root: str | os.PathLike[str],
    git_metadata_dir: str | os.PathLike[str],
    repository_map: RepositoryMap,
    seed_paths: Iterable[str],
    trusted_allowed_paths: Iterable[str],
    *,
    max_depth: int = 2,
    max_paths: int = 64,
) -> RepositoryContextPackage:
    """Build the accepted E2 package while keeping Git metadata outside the workspace."""
    if not isinstance(repository_map, RepositoryMap):
        raise RepositoryContextError("repository map object is invalid")
    try:
        serialize_repository_map(repository_map)
        rebuilt = build_detached_repository_map(
            repository_root,
            git_metadata_dir,
            repository_map.repository_sha,
        )
    except RepositoryMapError as exc:
        raise RepositoryContextError("repository map cannot be revalidated against detached Git metadata") from exc
    if rebuilt != repository_map:
        raise RepositoryContextError("repository map does not match the detached exact repository commit")

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
    try:
        reader = _reader(repository_root, git_metadata_dir)
        tree = _assert_metadata_identity(reader, repository_map.repository_sha)
    except RepositoryMapError as exc:
        raise RepositoryContextError("detached Git metadata boundary is invalid") from exc
    if tree != repository_map.tree_sha:
        raise RepositoryContextError("detached Git tree does not match repository map")

    files: list[RepositoryContextFile] = []
    total = 0
    for path in read_paths:
        entry = entries[path]
        raw = _context_blob(reader, entry.blob_sha, entry.size)
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
    digest = hashlib.sha256(_context_payload_bytes(package_without_digest)).hexdigest()
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
    try:
        final_tree = _assert_metadata_identity(reader, repository_map.repository_sha)
    except RepositoryMapError as exc:
        raise RepositoryContextError("detached Git metadata changed while building context") from exc
    if final_tree != repository_map.tree_sha:
        raise RepositoryContextError("detached Git tree changed while building context")
    return package

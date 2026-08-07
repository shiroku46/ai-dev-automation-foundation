#!/usr/bin/env python3
"""Build a bounded exact-SHA repository map from tracked Git objects only."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

MAX_TRACKED_FILES = 20_000
MAX_PATH_LENGTH = 240
MAX_TRACKED_BLOB_BYTES = 1_073_741_824
MAX_TOTAL_TRACKED_BYTES = 10_737_418_240
MAX_PYTHON_BLOB_BYTES = 1_048_576
MAX_IMPORTS_PER_FILE = 1_000
MAX_LOCAL_EDGES = 100_000
MAX_SERIALIZED_MAP_BYTES = 16_777_216
MAX_GIT_OUTPUT_BYTES = 16_777_216
MAX_GIT_DIAGNOSTIC_BYTES = 65_536
MAX_GIT_COMMAND_SECONDS = 30
MAX_IMPACT_DEPTH = 16
MAX_IMPACT_PATHS = 10_000
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:/")
_REGULAR_MODES = frozenset({"100644", "100755"})


class RepositoryMapError(ValueError):
    """Repository map input or exact-SHA evidence is unsafe or unbounded."""


@dataclass(frozen=True, order=True)
class RepositoryMapEntry:
    path: str
    blob_sha: str
    size: int
    executable: bool
    kind: str
    module: str | None
    imported_modules: tuple[str, ...]
    local_dependencies: tuple[str, ...]
    local_dependents: tuple[str, ...]


@dataclass(frozen=True)
class RepositoryMap:
    repository_sha: str
    tree_sha: str
    entries: tuple[RepositoryMapEntry, ...]
    map_sha256: str

    @property
    def file_count(self) -> int:
        return len(self.entries)

    @property
    def total_bytes(self) -> int:
        return sum(entry.size for entry in self.entries)


@dataclass(frozen=True)
class RepositoryImpact:
    seed_paths: tuple[str, ...]
    context_paths: tuple[str, ...]
    dependent_paths: tuple[str, ...]
    test_paths: tuple[str, ...]
    max_depth: int

    @property
    def all_paths(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.seed_paths) | set(self.context_paths) | set(self.dependent_paths)))


@dataclass(frozen=True)
class _TrackedFile:
    path: str
    blob_sha: str
    size: int
    executable: bool


@dataclass(frozen=True)
class _PythonImports:
    modules: tuple[str, ...]
    candidate_modules: tuple[str, ...]


def _git_executable() -> str:
    value = shutil.which("git")
    if not value:
        raise RepositoryMapError("git executable is unavailable")
    try:
        path = Path(value).resolve(strict=True)
    except OSError as exc:
        raise RepositoryMapError("git executable cannot be resolved") from exc
    if not path.is_file():
        raise RepositoryMapError("git executable is invalid")
    return str(path)


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


def _run_git(
    executable: str,
    repository_root: Path,
    args: list[str],
    *,
    output_limit: int = MAX_GIT_OUTPUT_BYTES,
) -> bytes:
    try:
        completed = subprocess.run(
            [executable, "-C", str(repository_root), *args],
            env=_git_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=MAX_GIT_COMMAND_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RepositoryMapError("local git command could not complete") from exc
    if len(completed.stdout) > output_limit:
        raise RepositoryMapError("local git command output exceeded its bounded limit")
    if len(completed.stderr) > MAX_GIT_DIAGNOSTIC_BYTES:
        raise RepositoryMapError("local git diagnostics exceeded their bounded limit")
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace")[:500]
        raise RepositoryMapError(f"local git command failed: {detail}")
    return completed.stdout


def _ascii_line(value: bytes, label: str) -> str:
    try:
        text = value.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise RepositoryMapError(f"{label} is not ASCII") from exc
    if not text or "\n" in text or "\r" in text:
        raise RepositoryMapError(f"{label} is invalid")
    return text


def _sha(value: bytes, label: str) -> str:
    text = _ascii_line(value, label)
    if _SHA_RE.fullmatch(text) is None:
        raise RepositoryMapError(f"{label} is not a lowercase SHA-1 identity")
    return text


def _safe_path(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_PATH_LENGTH
        or value != value.strip()
        or value.startswith(("/", "\\", "./", "../"))
        or _WINDOWS_DRIVE_RE.match(value)
        or "\\" in value
        or "//" in value
        or _CONTROL_RE.search(value) is not None
    ):
        raise RepositoryMapError("tracked repository path is unsafe")
    parts = value.split("/")
    if any(part in {"", ".", ".."} or part.casefold() == ".git" for part in parts):
        raise RepositoryMapError("tracked repository path is unsafe")
    return value


def _repository_root(value: str | os.PathLike[str]) -> Path:
    path = Path(value)
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RepositoryMapError("repository root is missing") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise RepositoryMapError("repository root is not a real directory")
    return resolved


def _tracked_files(executable: str, root: Path, repository_sha: str) -> tuple[_TrackedFile, ...]:
    raw = _run_git(
        executable,
        root,
        ["ls-tree", "-r", "-l", "-z", "--full-tree", repository_sha],
    )
    records = raw.split(b"\0")
    if records and records[-1] == b"":
        records.pop()
    if len(records) > MAX_TRACKED_FILES:
        raise RepositoryMapError("tracked file count exceeds its bounded limit")
    files: list[_TrackedFile] = []
    seen: set[str] = set()
    total_bytes = 0
    for record in records:
        try:
            header, path_raw = record.split(b"\t", 1)
            mode_raw, type_raw, sha_raw, size_raw = header.split(b" ", 3)
            mode = mode_raw.decode("ascii")
            object_type = type_raw.decode("ascii")
            blob_sha = sha_raw.decode("ascii")
            size_text = size_raw.decode("ascii")
            path = path_raw.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise RepositoryMapError("tracked Git tree entry is malformed or non-UTF-8") from exc
        path = _safe_path(path)
        folded = path.casefold()
        if folded in seen:
            raise RepositoryMapError("tracked repository paths are case-ambiguous or duplicated")
        seen.add(folded)
        if mode not in _REGULAR_MODES or object_type != "blob":
            raise RepositoryMapError("tracked repository contains an unsupported non-regular entry")
        if _SHA_RE.fullmatch(blob_sha) is None or not size_text.isascii() or not size_text.isdigit():
            raise RepositoryMapError("tracked Git blob identity or size is invalid")
        size = int(size_text)
        if size < 0 or size > MAX_TRACKED_BLOB_BYTES:
            raise RepositoryMapError("tracked Git blob exceeds its bounded per-file limit")
        total_bytes += size
        if total_bytes > MAX_TOTAL_TRACKED_BYTES:
            raise RepositoryMapError("tracked repository exceeds its bounded total-byte limit")
        files.append(_TrackedFile(path, blob_sha, size, mode == "100755"))
    files.sort(key=lambda item: item.path)
    return tuple(files)


def _module_name(path: str) -> str | None:
    if not path.endswith(".py"):
        return None
    pure = PurePosixPath(path)
    parts = list(pure.parts)
    filename = parts.pop()
    stem = filename[:-3]
    if stem == "__init__":
        if not parts:
            return None
        components = parts
    else:
        components = [*parts, stem]
    if not components or any(not component.isidentifier() for component in components):
        return None
    return ".".join(components)


def _package_for_module(path: str, module: str | None) -> tuple[str, ...]:
    if module is None:
        return ()
    parts = tuple(module.split("."))
    if path.endswith("/__init__.py"):
        return parts
    if path == "__init__.py":
        return ()
    return parts[:-1]


def _relative_base(path: str, module: str | None, level: int, imported: str | None) -> str:
    package = _package_for_module(path, module)
    if level < 1 or level > len(package) + 1:
        raise RepositoryMapError("relative Python import cannot be resolved from its package")
    keep = len(package) - (level - 1)
    if keep < 0:
        raise RepositoryMapError("relative Python import escapes its package")
    components = list(package[:keep])
    if imported:
        components.extend(imported.split("."))
    if not components:
        raise RepositoryMapError("relative Python import resolves outside a named package")
    return ".".join(components)


def _python_imports(path: str, module: str | None, content: bytes) -> _PythonImports:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RepositoryMapError("tracked Python blob is not UTF-8") from exc
    try:
        tree = ast.parse(text, filename=path)
    except (SyntaxError, ValueError, MemoryError, RecursionError) as exc:
        raise RepositoryMapError("tracked Python blob cannot be parsed safely") from exc
    observations: set[str] = set()
    candidates: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                observations.add(alias.name)
                candidates.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = _relative_base(path, module, node.level, node.module)
            else:
                if not node.module:
                    raise RepositoryMapError("absolute Python import-from lacks a module")
                base = node.module
            observations.add(base)
            candidates.add(base)
            for alias in node.names:
                if alias.name == "*":
                    continue
                named = f"{base}.{alias.name}"
                observations.add(named)
                candidates.add(named)
        if len(observations) > MAX_IMPORTS_PER_FILE or len(candidates) > MAX_IMPORTS_PER_FILE * 2:
            raise RepositoryMapError("tracked Python import count exceeds its bounded limit")
    return _PythonImports(tuple(sorted(observations)), tuple(sorted(candidates)))


def _read_python_blob(executable: str, root: Path, tracked: _TrackedFile) -> bytes:
    if tracked.size > MAX_PYTHON_BLOB_BYTES:
        raise RepositoryMapError("tracked Python blob exceeds its bounded parse limit")
    raw = _run_git(
        executable,
        root,
        ["cat-file", "blob", tracked.blob_sha],
        output_limit=MAX_PYTHON_BLOB_BYTES + 1,
    )
    if len(raw) != tracked.size:
        raise RepositoryMapError("tracked Python blob size changed or is inconsistent")
    if hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest() != tracked.blob_sha:
        raise RepositoryMapError("tracked Python blob bytes do not match the Git object identity")
    return raw


def _map_payload(
    repository_sha: str,
    tree_sha: str,
    entries: Iterable[RepositoryMapEntry],
) -> dict[str, object]:
    return {
        "entries": [
            {
                "blob_sha": entry.blob_sha,
                "executable": entry.executable,
                "imported_modules": list(entry.imported_modules),
                "kind": entry.kind,
                "local_dependencies": list(entry.local_dependencies),
                "local_dependents": list(entry.local_dependents),
                "module": entry.module,
                "path": entry.path,
                "size": entry.size,
            }
            for entry in entries
        ],
        "repository_sha": repository_sha,
        "schema_version": 1,
        "tree_sha": tree_sha,
    }


def _payload_bytes(repository_sha: str, tree_sha: str, entries: Iterable[RepositoryMapEntry]) -> bytes:
    raw = json.dumps(
        _map_payload(repository_sha, tree_sha, entries),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    if not raw or len(raw) > MAX_SERIALIZED_MAP_BYTES:
        raise RepositoryMapError("repository map payload exceeds its bounded serialized limit")
    return raw


def build_repository_map(
    repository_root: str | os.PathLike[str],
    expected_sha: str,
) -> RepositoryMap:
    """Build one immutable map from exact tracked Git objects without reading working-tree file bytes."""
    if not isinstance(expected_sha, str) or _SHA_RE.fullmatch(expected_sha) is None:
        raise RepositoryMapError("expected repository SHA is invalid")
    root = _repository_root(repository_root)
    executable = _git_executable()
    object_format = _ascii_line(_run_git(executable, root, ["rev-parse", "--show-object-format"]), "Git object format")
    if object_format != "sha1":
        raise RepositoryMapError("repository map currently requires SHA-1 Git object identity")
    top = _ascii_line(_run_git(executable, root, ["rev-parse", "--show-toplevel"]), "Git top-level path")
    try:
        top_path = Path(top).resolve(strict=True)
    except OSError as exc:
        raise RepositoryMapError("Git top-level path cannot be resolved") from exc
    if top_path != root:
        raise RepositoryMapError("repository root does not match the Git top-level directory")
    repository_sha = _sha(_run_git(executable, root, ["rev-parse", "HEAD"]), "repository HEAD SHA")
    if repository_sha != expected_sha:
        raise RepositoryMapError("repository HEAD SHA does not match expected identity")
    tree_sha = _sha(_run_git(executable, root, ["rev-parse", f"{repository_sha}^{{tree}}"]), "repository tree SHA")
    tracked = _tracked_files(executable, root, repository_sha)

    module_to_path: dict[str, str] = {}
    module_by_path: dict[str, str | None] = {}
    for item in tracked:
        module = _module_name(item.path)
        module_by_path[item.path] = module
        if module is not None:
            if module in module_to_path:
                raise RepositoryMapError("tracked Python module identity is ambiguous")
            module_to_path[module] = item.path

    imports_by_path: dict[str, _PythonImports] = {}
    dependencies_by_path: dict[str, tuple[str, ...]] = {}
    edge_count = 0
    for item in tracked:
        module = module_by_path[item.path]
        if item.path.endswith(".py"):
            parsed = _python_imports(item.path, module, _read_python_blob(executable, root, item))
        else:
            parsed = _PythonImports((), ())
        imports_by_path[item.path] = parsed
        dependencies = tuple(sorted({
            module_to_path[candidate]
            for candidate in parsed.candidate_modules
            if candidate in module_to_path and module_to_path[candidate] != item.path
        }))
        edge_count += len(dependencies)
        if edge_count > MAX_LOCAL_EDGES:
            raise RepositoryMapError("local dependency edge count exceeds its bounded limit")
        dependencies_by_path[item.path] = dependencies

    dependents: dict[str, set[str]] = {item.path: set() for item in tracked}
    for source, dependencies in dependencies_by_path.items():
        for dependency in dependencies:
            dependents[dependency].add(source)

    entries = tuple(
        RepositoryMapEntry(
            path=item.path,
            blob_sha=item.blob_sha,
            size=item.size,
            executable=item.executable,
            kind="python" if item.path.endswith(".py") else "file",
            module=module_by_path[item.path],
            imported_modules=imports_by_path[item.path].modules,
            local_dependencies=dependencies_by_path[item.path],
            local_dependents=tuple(sorted(dependents[item.path])),
        )
        for item in tracked
    )
    payload = _payload_bytes(repository_sha, tree_sha, entries)
    return RepositoryMap(repository_sha, tree_sha, entries, hashlib.sha256(payload).hexdigest())


def serialize_repository_map(repository_map: RepositoryMap) -> bytes:
    """Return bounded canonical JSON including the non-self-referential payload digest."""
    if not isinstance(repository_map, RepositoryMap):
        raise RepositoryMapError("repository map object is invalid")
    payload = _payload_bytes(repository_map.repository_sha, repository_map.tree_sha, repository_map.entries)
    observed = hashlib.sha256(payload).hexdigest()
    if observed != repository_map.map_sha256:
        raise RepositoryMapError("repository map digest does not match its canonical payload")
    value = _map_payload(repository_map.repository_sha, repository_map.tree_sha, repository_map.entries)
    value["map_sha256"] = repository_map.map_sha256
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(raw) > MAX_SERIALIZED_MAP_BYTES:
        raise RepositoryMapError("repository map serialization exceeds its bounded limit")
    return raw


def _is_test_path(path: str) -> bool:
    parts = path.split("/")
    return "tests" in parts or (parts[-1].startswith("test_") and parts[-1].endswith(".py"))


def _expand(
    adjacency: dict[str, tuple[str, ...]],
    seeds: tuple[str, ...],
    max_depth: int,
    max_paths: int,
) -> tuple[str, ...]:
    visited = set(seeds)
    frontier = set(seeds)
    discovered: set[str] = set()
    for _ in range(max_depth):
        following: set[str] = set()
        for path in sorted(frontier):
            following.update(adjacency[path])
        following -= visited
        if not following:
            break
        discovered.update(following)
        visited.update(following)
        if len(visited) > max_paths:
            raise RepositoryMapError("impact expansion exceeds its bounded path limit")
        frontier = following
    return tuple(sorted(discovered))


def discover_repository_impact(
    repository_map: RepositoryMap,
    seed_paths: Iterable[str],
    *,
    max_depth: int = 2,
    max_paths: int = 256,
) -> RepositoryImpact:
    """Return bounded advisory read/check context; never mutation authorization."""
    if not isinstance(repository_map, RepositoryMap):
        raise RepositoryMapError("repository map object is invalid")
    if isinstance(max_depth, bool) or not isinstance(max_depth, int) or not 0 <= max_depth <= MAX_IMPACT_DEPTH:
        raise RepositoryMapError("impact depth is invalid")
    if isinstance(max_paths, bool) or not isinstance(max_paths, int) or not 1 <= max_paths <= MAX_IMPACT_PATHS:
        raise RepositoryMapError("impact path limit is invalid")
    try:
        seeds = tuple(seed_paths)
    except TypeError as exc:
        raise RepositoryMapError("impact seed paths are invalid") from exc
    if not seeds:
        raise RepositoryMapError("impact seed paths are empty")
    if seeds != tuple(sorted(seeds)) or len(set(seeds)) != len(seeds):
        raise RepositoryMapError("impact seed paths must be sorted and unique")
    entries = {entry.path: entry for entry in repository_map.entries}
    if len(seeds) > max_paths:
        raise RepositoryMapError("impact seed count exceeds its bounded path limit")
    for seed in seeds:
        _safe_path(seed)
        if seed not in entries:
            raise RepositoryMapError("impact seed path is not present in the repository map")
    forward = {path: entries[path].local_dependencies for path in entries}
    reverse = {path: entries[path].local_dependents for path in entries}
    context = _expand(forward, seeds, max_depth, max_paths)
    dependents = _expand(reverse, seeds, max_depth, max_paths)
    all_paths = set(seeds) | set(context) | set(dependents)
    if len(all_paths) > max_paths:
        raise RepositoryMapError("impact result exceeds its bounded path limit")
    tests = tuple(sorted(path for path in all_paths if _is_test_path(path)))
    return RepositoryImpact(seeds, context, dependents, tests, max_depth)

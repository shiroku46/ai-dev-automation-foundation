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
_DRIVE_RE = re.compile(r"^[A-Za-z]:/")
_REGULAR_MODES = {"100644", "100755"}


class RepositoryMapError(ValueError):
    pass


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
        return sum(item.size for item in self.entries)


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
class _Imports:
    observed: tuple[str, ...]
    candidates: tuple[str, ...]


def _git_executable() -> str:
    value = shutil.which("git")
    if not value:
        raise RepositoryMapError("git executable is unavailable")
    try:
        resolved = Path(value).resolve(strict=True)
    except OSError as exc:
        raise RepositoryMapError("git executable cannot be resolved") from exc
    if not resolved.is_file():
        raise RepositoryMapError("git executable is invalid")
    return str(resolved)


def _git_env() -> dict[str, str]:
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TMP", "TEMP", "TMPDIR") if key in os.environ}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "LC_ALL": "C", "LANG": "C"})
    return env


def _git(executable: str, root: Path, args: list[str], *, limit: int = MAX_GIT_OUTPUT_BYTES) -> bytes:
    try:
        completed = subprocess.run(
            [executable, "-C", str(root), *args],
            env=_git_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=MAX_GIT_COMMAND_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RepositoryMapError("local git command could not complete") from exc
    if len(completed.stdout) > limit or len(completed.stderr) > MAX_GIT_DIAGNOSTIC_BYTES:
        raise RepositoryMapError("local git command exceeded bounded output")
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace")[:500]
        raise RepositoryMapError(f"local git command failed: {detail}")
    return completed.stdout


def _line(raw: bytes, label: str, *, ascii_only: bool = True) -> str:
    try:
        value = raw.decode("ascii" if ascii_only else "utf-8").strip()
    except UnicodeDecodeError as exc:
        raise RepositoryMapError(f"{label} has invalid encoding") from exc
    if not value or "\n" in value or "\r" in value or "\x00" in value:
        raise RepositoryMapError(f"{label} is invalid")
    return value


def _sha(raw: bytes, label: str) -> str:
    value = _line(raw, label)
    if _SHA_RE.fullmatch(value) is None:
        raise RepositoryMapError(f"{label} is not a lowercase SHA-1")
    return value


def _safe_path(value: str) -> str:
    if (
        not value or len(value) > MAX_PATH_LENGTH or value != value.strip()
        or value.startswith(("/", "\\", "./", "../")) or _DRIVE_RE.match(value)
        or "\\" in value or "//" in value or _CONTROL_RE.search(value)
    ):
        raise RepositoryMapError("tracked repository path is unsafe")
    parts = value.split("/")
    if any(part in {"", ".", ".."} or part.casefold() == ".git" for part in parts):
        raise RepositoryMapError("tracked repository path is unsafe")
    return value


def _root(value: str | os.PathLike[str]) -> Path:
    path = Path(value)
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RepositoryMapError("repository root is missing") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise RepositoryMapError("repository root is not a real directory")
    return resolved


def _tracked(executable: str, root: Path, commit: str) -> tuple[_TrackedFile, ...]:
    raw = _git(executable, root, ["ls-tree", "-r", "-l", "-z", "--full-tree", commit])
    records = raw.split(b"\0")
    if records and not records[-1]:
        records.pop()
    if len(records) > MAX_TRACKED_FILES:
        raise RepositoryMapError("tracked file count exceeds limit")
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
            raise RepositoryMapError("tracked Git tree entry is malformed") from exc
        path = _safe_path(path)
        folded = path.casefold()
        if folded in seen:
            raise RepositoryMapError("tracked paths are duplicated or case-ambiguous")
        seen.add(folded)
        if mode not in _REGULAR_MODES or object_type != "blob":
            raise RepositoryMapError("tracked repository contains a non-regular entry")
        if _SHA_RE.fullmatch(object_sha) is None or not size_raw.isdigit():
            raise RepositoryMapError("tracked blob identity or size is invalid")
        size = int(size_raw)
        if size > MAX_TRACKED_BLOB_BYTES:
            raise RepositoryMapError("tracked blob exceeds per-file limit")
        total += size
        if total > MAX_TOTAL_TRACKED_BYTES:
            raise RepositoryMapError("tracked repository exceeds total-byte limit")
        result.append(_TrackedFile(path, object_sha, size, mode == "100755"))
    return tuple(sorted(result, key=lambda item: item.path))


def _module(path: str) -> str | None:
    if not path.endswith(".py"):
        return None
    pure = PurePosixPath(path)
    parts = list(pure.parts)
    stem = parts.pop()[:-3]
    components = parts if stem == "__init__" else [*parts, stem]
    if not components or any(not component.isidentifier() for component in components):
        return None
    return ".".join(components)


def _package(path: str, module: str | None) -> tuple[str, ...]:
    if module is None:
        return ()
    parts = tuple(module.split("."))
    return parts if path.endswith("/__init__.py") else parts[:-1]


def _relative_base(path: str, module: str | None, level: int, imported: str | None) -> str:
    package = _package(path, module)
    if level < 1 or not package or level > len(package):
        raise RepositoryMapError("relative Python import escapes its package")
    components = list(package[: len(package) - (level - 1)])
    if imported:
        components.extend(imported.split("."))
    if not components:
        raise RepositoryMapError("relative Python import has no local package base")
    return ".".join(components)


def _parse_imports(path: str, module: str | None, raw: bytes) -> _Imports:
    try:
        tree = ast.parse(raw.decode("utf-8"), filename=path)
    except (UnicodeDecodeError, SyntaxError, ValueError, MemoryError, RecursionError) as exc:
        raise RepositoryMapError("tracked Python blob cannot be parsed safely") from exc
    observed: set[str] = set()
    candidates: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                observed.add(alias.name)
                candidates.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = _relative_base(path, module, node.level, node.module)
            elif node.module:
                base = node.module
            else:
                raise RepositoryMapError("absolute import-from lacks a module")
            observed.add(base)
            candidates.add(base)
            for alias in node.names:
                if alias.name != "*":
                    named = f"{base}.{alias.name}"
                    observed.add(named)
                    candidates.add(named)
        if len(observed) > MAX_IMPORTS_PER_FILE or len(candidates) > MAX_IMPORTS_PER_FILE * 2:
            raise RepositoryMapError("Python import count exceeds limit")
    return _Imports(tuple(sorted(observed)), tuple(sorted(candidates)))


def _read_python(executable: str, root: Path, tracked: _TrackedFile) -> bytes:
    if tracked.size > MAX_PYTHON_BLOB_BYTES:
        raise RepositoryMapError("tracked Python blob exceeds parse limit")
    raw = _git(executable, root, ["cat-file", "blob", tracked.blob_sha], limit=MAX_PYTHON_BLOB_BYTES + 1)
    if len(raw) != tracked.size:
        raise RepositoryMapError("tracked Python blob size is inconsistent")
    git_object = b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw
    if hashlib.sha1(git_object).hexdigest() != tracked.blob_sha:
        raise RepositoryMapError("tracked Python blob bytes do not match Git identity")
    return raw


def _payload(commit: str, tree: str, entries: Iterable[RepositoryMapEntry]) -> dict[str, object]:
    return {
        "entries": [{
            "blob_sha": item.blob_sha,
            "executable": item.executable,
            "imported_modules": list(item.imported_modules),
            "kind": item.kind,
            "local_dependencies": list(item.local_dependencies),
            "local_dependents": list(item.local_dependents),
            "module": item.module,
            "path": item.path,
            "size": item.size,
        } for item in entries],
        "repository_sha": commit,
        "schema_version": 1,
        "tree_sha": tree,
    }


def _payload_bytes(commit: str, tree: str, entries: Iterable[RepositoryMapEntry]) -> bytes:
    raw = json.dumps(_payload(commit, tree, entries), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if not raw or len(raw) > MAX_SERIALIZED_MAP_BYTES:
        raise RepositoryMapError("repository map serialization exceeds limit")
    return raw


def build_repository_map(repository_root: str | os.PathLike[str], expected_sha: str) -> RepositoryMap:
    if not isinstance(expected_sha, str) or _SHA_RE.fullmatch(expected_sha) is None:
        raise RepositoryMapError("expected repository SHA is invalid")
    root = _root(repository_root)
    executable = _git_executable()
    if _line(_git(executable, root, ["rev-parse", "--show-object-format"]), "Git object format") != "sha1":
        raise RepositoryMapError("repository map requires SHA-1 Git objects")
    top = Path(_line(_git(executable, root, ["rev-parse", "--show-toplevel"]), "Git top-level", ascii_only=False)).resolve(strict=True)
    if top != root:
        raise RepositoryMapError("repository root does not match Git top-level")
    commit = _sha(_git(executable, root, ["rev-parse", "HEAD"]), "repository HEAD")
    if commit != expected_sha:
        raise RepositoryMapError("repository HEAD does not match expected SHA")
    tree = _sha(_git(executable, root, ["rev-parse", f"{commit}^{{tree}}"]), "repository tree")
    tracked = _tracked(executable, root, commit)

    module_by_path = {item.path: _module(item.path) for item in tracked}
    module_to_path: dict[str, str] = {}
    for path, module in module_by_path.items():
        if module is not None:
            if module in module_to_path:
                raise RepositoryMapError("tracked Python module identity is ambiguous")
            module_to_path[module] = path

    imports: dict[str, _Imports] = {}
    deps: dict[str, tuple[str, ...]] = {}
    edge_count = 0
    for item in tracked:
        parsed = _parse_imports(item.path, module_by_path[item.path], _read_python(executable, root, item)) if item.path.endswith(".py") else _Imports((), ())
        imports[item.path] = parsed
        local = tuple(sorted({module_to_path[name] for name in parsed.candidates if name in module_to_path and module_to_path[name] != item.path}))
        edge_count += len(local)
        if edge_count > MAX_LOCAL_EDGES:
            raise RepositoryMapError("local dependency edge count exceeds limit")
        deps[item.path] = local

    reverse = {item.path: set() for item in tracked}
    for source, dependencies in deps.items():
        for dependency in dependencies:
            reverse[dependency].add(source)
    entries = tuple(RepositoryMapEntry(
        path=item.path,
        blob_sha=item.blob_sha,
        size=item.size,
        executable=item.executable,
        kind="python" if item.path.endswith(".py") else "file",
        module=module_by_path[item.path],
        imported_modules=imports[item.path].observed,
        local_dependencies=deps[item.path],
        local_dependents=tuple(sorted(reverse[item.path])),
    ) for item in tracked)
    raw = _payload_bytes(commit, tree, entries)
    return RepositoryMap(commit, tree, entries, hashlib.sha256(raw).hexdigest())


def serialize_repository_map(repository_map: RepositoryMap) -> bytes:
    if not isinstance(repository_map, RepositoryMap):
        raise RepositoryMapError("repository map object is invalid")
    payload = _payload_bytes(repository_map.repository_sha, repository_map.tree_sha, repository_map.entries)
    if hashlib.sha256(payload).hexdigest() != repository_map.map_sha256:
        raise RepositoryMapError("repository map digest does not match payload")
    value = _payload(repository_map.repository_sha, repository_map.tree_sha, repository_map.entries)
    value["map_sha256"] = repository_map.map_sha256
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(raw) > MAX_SERIALIZED_MAP_BYTES:
        raise RepositoryMapError("repository map serialization exceeds limit")
    return raw


def _expand(adjacency: dict[str, tuple[str, ...]], seeds: tuple[str, ...], depth: int, limit: int) -> tuple[str, ...]:
    visited, frontier, found = set(seeds), set(seeds), set()
    for _ in range(depth):
        next_paths = {candidate for path in frontier for candidate in adjacency[path]} - visited
        if not next_paths:
            break
        visited.update(next_paths)
        found.update(next_paths)
        if len(visited) > limit:
            raise RepositoryMapError("impact expansion exceeds path limit")
        frontier = next_paths
    return tuple(sorted(found))


def _is_test(path: str) -> bool:
    parts = path.split("/")
    return "tests" in parts or (parts[-1].startswith("test_") and parts[-1].endswith(".py"))


def discover_repository_impact(repository_map: RepositoryMap, seed_paths: Iterable[str], *, max_depth: int = 2, max_paths: int = 256) -> RepositoryImpact:
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
    if not seeds or seeds != tuple(sorted(seeds)) or len(set(seeds)) != len(seeds):
        raise RepositoryMapError("impact seed paths must be non-empty, sorted, and unique")
    entries = {item.path: item for item in repository_map.entries}
    if len(seeds) > max_paths:
        raise RepositoryMapError("impact seed count exceeds path limit")
    for seed in seeds:
        if _safe_path(seed) not in entries:
            raise RepositoryMapError("impact seed path is absent from repository map")
    forward = {path: item.local_dependencies for path, item in entries.items()}
    backward = {path: item.local_dependents for path, item in entries.items()}
    context = _expand(forward, seeds, max_depth, max_paths)
    dependents = _expand(backward, seeds, max_depth, max_paths)
    union = set(seeds) | set(context) | set(dependents)
    if len(union) > max_paths:
        raise RepositoryMapError("impact result exceeds path limit")
    tests = tuple(sorted(path for path in union if _is_test(path)))
    return RepositoryImpact(seeds, context, dependents, tests, max_depth)

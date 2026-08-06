#!/usr/bin/env python3
"""Deterministic, non-executing evaluation-suite validation."""
from __future__ import annotations

import hashlib, json, os, re, stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.agent_eval_task_contract import (
    MAX_MANIFEST_BYTES, EvaluationTaskError, parse_evaluation_task,
)

SCHEMA_VERSION = BUNDLE_INDEX_VERSION = 1
MAX_CATALOG_BYTES, MAX_TASKS, MAX_PATH_LENGTH = 1_048_576, 1_000, 240
MAX_BUNDLE_FILES, MAX_BUNDLE_BYTES, MAX_FILE_BYTES = 100_000, 10_737_418_240, 1_073_741_824
TOP_LEVEL_KEYS = frozenset({"schema_version", "suite_id", "suite_version", "foundation_sha", "task_count", "tasks"})
TASK_ENTRY_KEYS = frozenset({"task_id", "task_version", "manifest_path", "manifest_sha256", "fixture_root", "grader_root"})
_ID, _SHA, _DIGEST = (re.compile(p) for p in (r"^[a-z0-9](?:[a-z0-9._-]{0,127})$", r"^[0-9a-f]{40}$", r"^[0-9a-f]{64}$"))
_DRIVE, _CONTROL = re.compile(r"^[A-Za-z]:/"), re.compile(r"[\x00-\x1f\x7f]")


class EvaluationSuiteError(ValueError):
    pass


@dataclass(frozen=True)
class SuiteTaskEntry:
    task_id: str
    task_version: int
    manifest_path: str
    manifest_sha256: str
    fixture_root: str
    grader_root: str


@dataclass(frozen=True)
class EvaluationSuiteCatalog:
    suite_id: str
    suite_version: int
    foundation_sha: str
    tasks: tuple[SuiteTaskEntry, ...]
    catalog_sha256: str

    @property
    def task_count(self): return len(self.tasks)


@dataclass(frozen=True)
class BundleFile:
    path: str
    size: int
    sha256: str
    executable: bool


@dataclass(frozen=True)
class DirectoryBundle:
    sha256: str
    files: tuple[BundleFile, ...]
    uncompressed_bytes: int

    @property
    def file_count(self): return len(self.files)


@dataclass(frozen=True)
class ValidatedSuiteTask:
    entry: SuiteTaskEntry
    manifest: Any
    fixture_bundle: DirectoryBundle
    grader_bundle: DirectoryBundle


@dataclass(frozen=True)
class EvaluationSuite:
    catalog: EvaluationSuiteCatalog
    tasks: tuple[ValidatedSuiteTask, ...]


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out: raise EvaluationSuiteError("duplicate JSON member")
        out[key] = value
    return out


def _constant(value): raise EvaluationSuiteError(f"non-standard number: {value}")


def _obj(value, keys, label):
    if not isinstance(value, dict) or set(value) != keys: raise EvaluationSuiteError(f"invalid {label} keys")
    return value


def _int(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high: raise EvaluationSuiteError(f"invalid {label}")
    return value


def _match(value, pattern, label):
    if not isinstance(value, str) or pattern.fullmatch(value) is None: raise EvaluationSuiteError(f"invalid {label}")
    return value


def _path(value, prefix, label, suffix=""):
    invalid = (
        not isinstance(value, str) or not value or len(value) > MAX_PATH_LENGTH
        or value != value.strip() or _CONTROL.search(value)
        or value.startswith(("/", "\\", "./", "../")) or _DRIVE.match(value)
        or "\\" in value or "//" in value or any(c in value for c in "*?[]{}")
    )
    if invalid: raise EvaluationSuiteError(f"invalid {label}")
    if any(p in {"", ".", ".."} or p.casefold() == ".git" for p in value.split("/")): raise EvaluationSuiteError(f"unsafe {label}")
    if not value.startswith(prefix + "/") or (suffix and not value.endswith(suffix)): raise EvaluationSuiteError(f"invalid {label} location")
    return value


def _entry(value):
    item = _obj(value, TASK_ENTRY_KEYS, "task entry")
    return SuiteTaskEntry(
        _match(item["task_id"], _ID, "task ID"),
        _int(item["task_version"], "task version", 1, 1_000_000_000),
        _path(item["manifest_path"], "tasks", "manifest path", ".json"),
        _match(item["manifest_sha256"], _DIGEST, "manifest digest"),
        _path(item["fixture_root"], "fixtures", "fixture root"),
        _path(item["grader_root"], "graders", "grader root"),
    )


def parse_evaluation_suite_catalog(content):
    """Parse exact canonical bytes without reading referenced files."""
    try: raw = content.encode() if isinstance(content, str) else content
    except UnicodeEncodeError as exc: raise EvaluationSuiteError("catalog is not UTF-8") from exc
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_CATALOG_BYTES: raise EvaluationSuiteError("invalid catalog size")
    try: value = json.loads(raw.decode(), object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc: raise EvaluationSuiteError("malformed catalog") from exc
    data = _obj(value, TOP_LEVEL_KEYS, "catalog")
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    if raw != canonical: raise EvaluationSuiteError("non-canonical catalog")
    if type(data["schema_version"]) is not int or data["schema_version"] != SCHEMA_VERSION: raise EvaluationSuiteError("unsupported schema")
    values = data["tasks"]
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_TASKS: raise EvaluationSuiteError("invalid tasks")
    entries = tuple(_entry(value) for value in values)
    if _int(data["task_count"], "task count", 1, MAX_TASKS) != len(entries): raise EvaluationSuiteError("task count mismatch")
    ids = tuple((e.task_id, e.task_version) for e in entries)
    if ids != tuple(sorted(ids)) or len(set(ids)) != len(ids): raise EvaluationSuiteError("unsorted or duplicate tasks")
    for label, paths, overlap in (
        ("manifest paths", [e.manifest_path for e in entries], False),
        ("fixture roots", [e.fixture_root for e in entries], True),
        ("grader roots", [e.grader_root for e in entries], True),
    ):
        folded = sorted(path.casefold() for path in paths)
        if len(set(folded)) != len(folded): raise EvaluationSuiteError(f"duplicate {label}")
        if overlap and any(b.startswith(a + "/") for i, a in enumerate(folded) for b in folded[i + 1:]): raise EvaluationSuiteError(f"overlapping {label}")
    return EvaluationSuiteCatalog(
        _match(data["suite_id"], _ID, "suite ID"),
        _int(data["suite_version"], "suite version", 1, 1_000_000_000),
        _match(data["foundation_sha"], _SHA, "Foundation SHA"), entries,
        hashlib.sha256(raw).hexdigest(),
    )


def _root(value, label):
    path = Path(value)
    try: info, resolved = path.lstat(), path.resolve(strict=True)
    except OSError as exc: raise EvaluationSuiteError(f"missing {label}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode): raise EvaluationSuiteError(f"invalid {label}")
    return resolved


def _below(root, relative, kind, label):
    current = root
    try:
        for part in relative.split("/"):
            current /= part
            if stat.S_ISLNK(current.lstat().st_mode): raise EvaluationSuiteError(f"symlink {label}")
        resolved = current.resolve(strict=True); resolved.relative_to(root); info = resolved.stat()
    except (OSError, ValueError) as exc: raise EvaluationSuiteError(f"invalid {label}") from exc
    if kind == "file" and not stat.S_ISREG(info.st_mode): raise EvaluationSuiteError(f"invalid {label}")
    if kind == "directory" and not stat.S_ISDIR(info.st_mode): raise EvaluationSuiteError(f"invalid {label}")
    return resolved


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, stat.S_IMODE(info.st_mode), info.st_nlink,
            getattr(info, "st_mtime_ns", None), getattr(info, "st_ctime_ns", None))


def _hash_file(path, expected):
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or _identity(before) != _identity(expected): raise EvaluationSuiteError("file changed before inspection")
        digest, total = hashlib.sha256(), 0
        with os.fdopen(fd, "rb", closefd=True) as handle:
            fd = None
            while chunk := handle.read(1_048_576):
                total += len(chunk)
                if total > MAX_FILE_BYTES: raise EvaluationSuiteError("file exceeds limit")
                digest.update(chunk)
            after = os.fstat(handle.fileno())
        if total != before.st_size or _identity(after) != _identity(before): raise EvaluationSuiteError("file changed during inspection")
        return digest.hexdigest(), bool(before.st_mode & 0o111)
    except EvaluationSuiteError: raise
    except OSError as exc: raise EvaluationSuiteError("unreadable file") from exc
    finally:
        if fd is not None: os.close(fd)


def inspect_directory_bundle(value):
    """Hash a local tree without importing or executing its contents."""
    root, files, seen, total = _root(value, "bundle root"), [], set(), 0
    def visit(directory):
        nonlocal total
        try: children = sorted(os.scandir(directory), key=lambda child: child.name)
        except OSError as exc: raise EvaluationSuiteError("unreadable directory") from exc
        for child in children:
            path = Path(child.path)
            try: info = child.stat(follow_symlinks=False)
            except OSError as exc: raise EvaluationSuiteError("unreadable entry") from exc
            if stat.S_ISLNK(info.st_mode): raise EvaluationSuiteError("symlink in bundle")
            relative = path.relative_to(root).as_posix(); folded = relative.casefold()
            if folded in seen: raise EvaluationSuiteError("case-ambiguous path")
            seen.add(folded)
            if stat.S_ISDIR(info.st_mode): visit(path); continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1: raise EvaluationSuiteError("non-regular or hard-linked file")
            if info.st_size < 0 or info.st_size > MAX_FILE_BYTES: raise EvaluationSuiteError("invalid file size")
            total += info.st_size
            if total > MAX_BUNDLE_BYTES or len(files) >= MAX_BUNDLE_FILES: raise EvaluationSuiteError("bundle exceeds limits")
            sha, executable = _hash_file(path, info)
            files.append(BundleFile(relative, info.st_size, sha, executable))
    visit(root)
    if not files: raise EvaluationSuiteError("empty bundle")
    files.sort(key=lambda item: item.path)
    index = {"files": [{"executable": f.executable, "path": f.path, "sha256": f.sha256, "size": f.size} for f in files], "schema_version": BUNDLE_INDEX_VERSION}
    encoded = json.dumps(index, sort_keys=True, separators=(",", ":")).encode()
    return DirectoryBundle(hashlib.sha256(encoded).hexdigest(), tuple(files), total)


def _read(path, maximum, label):
    try:
        with path.open("rb") as handle: content = handle.read(maximum + 1)
    except OSError as exc: raise EvaluationSuiteError(f"unreadable {label}") from exc
    if not content or len(content) > maximum: raise EvaluationSuiteError(f"invalid {label} size")
    return content


def load_evaluation_suite(content, suite_root):
    """Validate catalog, manifests, bundles, and entrypoints without execution."""
    catalog, root, tasks = parse_evaluation_suite_catalog(content), _root(suite_root, "suite root"), []
    for entry in catalog.tasks:
        raw = _read(_below(root, entry.manifest_path, "file", "manifest"), MAX_MANIFEST_BYTES, "manifest")
        if hashlib.sha256(raw).hexdigest() != entry.manifest_sha256: raise EvaluationSuiteError("manifest digest mismatch")
        try: manifest = parse_evaluation_task(raw)
        except EvaluationTaskError as exc: raise EvaluationSuiteError("invalid manifest") from exc
        if (manifest.task_id, manifest.task_version, manifest.manifest_sha256) != (entry.task_id, entry.task_version, entry.manifest_sha256): raise EvaluationSuiteError("manifest identity mismatch")
        fixture = inspect_directory_bundle(_below(root, entry.fixture_root, "directory", "fixture root"))
        grader_root = _below(root, entry.grader_root, "directory", "grader root")
        grader = inspect_directory_bundle(grader_root); expected = manifest.fixture_bundle
        if (fixture.sha256, fixture.file_count, fixture.uncompressed_bytes) != (expected.sha256, expected.file_count, expected.uncompressed_bytes): raise EvaluationSuiteError("fixture identity mismatch")
        if grader.sha256 != manifest.grader.sha256: raise EvaluationSuiteError("grader identity mismatch")
        _below(grader_root, manifest.grader.entrypoint, "file", "grader entrypoint")
        tasks.append(ValidatedSuiteTask(entry, manifest, fixture, grader))
    return EvaluationSuite(catalog, tuple(tasks))

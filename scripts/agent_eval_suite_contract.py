#!/usr/bin/env python3
"""Deterministic, non-executing evaluation-suite validation."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.agent_eval_task_contract import (
    MAX_MANIFEST_BYTES,
    EvaluationTaskError,
    parse_evaluation_task,
)

SCHEMA_VERSION = 1
BUNDLE_INDEX_VERSION = 1
MAX_CATALOG_BYTES = 1_048_576
MAX_TASKS = 1_000
MAX_PATH_LENGTH = 240
MAX_BUNDLE_FILES = 100_000
MAX_BUNDLE_BYTES = 10_737_418_240
MAX_FILE_BYTES = 1_073_741_824

TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "suite_id",
        "suite_version",
        "foundation_sha",
        "task_count",
        "tasks",
    }
)
TASK_ENTRY_KEYS = frozenset(
    {
        "task_id",
        "task_version",
        "manifest_path",
        "manifest_sha256",
        "fixture_root",
        "grader_root",
    }
)

_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,127})$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:/")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_GLOB_CHARACTERS = frozenset("*?[]{}")


class EvaluationSuiteError(ValueError):
    """The suite catalog or checked-in bundle layout is unsafe or invalid."""


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
    def task_count(self) -> int:
        return len(self.tasks)


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
    def file_count(self) -> int:
        return len(self.files)


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


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvaluationSuiteError("catalog contains a duplicate JSON member")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise EvaluationSuiteError(f"non-standard JSON number is not allowed: {value}")


def _expect_object(
    value: Any,
    keys: frozenset[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise EvaluationSuiteError(f"{label} keys are invalid")
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
        raise EvaluationSuiteError(f"{label} is invalid")
    return value


def _expect_match(value: Any, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise EvaluationSuiteError(f"{label} is invalid")
    return value


def _expect_catalog_path(
    value: Any,
    prefix: str,
    label: str,
    *,
    suffix: str = "",
) -> str:
    invalid = (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_PATH_LENGTH
        or value != value.strip()
        or _CONTROL_RE.search(value) is not None
        or value.startswith(("/", "\\", "./", "../"))
        or _WINDOWS_DRIVE_RE.match(value) is not None
        or "\\" in value
        or "//" in value
        or any(character in value for character in _GLOB_CHARACTERS)
    )
    if invalid:
        raise EvaluationSuiteError(f"{label} is invalid")

    parts = value.split("/")
    if any(
        part in {"", ".", ".."} or part.casefold() == ".git"
        for part in parts
    ):
        raise EvaluationSuiteError(f"{label} is unsafe")
    if not value.startswith(prefix + "/"):
        raise EvaluationSuiteError(f"{label} is outside {prefix}/")
    if suffix and not value.endswith(suffix):
        raise EvaluationSuiteError(f"{label} must end with {suffix}")
    return value


def _parse_task_entry(value: Any) -> SuiteTaskEntry:
    item = _expect_object(value, TASK_ENTRY_KEYS, "task entry")
    return SuiteTaskEntry(
        task_id=_expect_match(item["task_id"], _ID_RE, "task ID"),
        task_version=_expect_integer(
            item["task_version"],
            "task version",
            1,
            1_000_000_000,
        ),
        manifest_path=_expect_catalog_path(
            item["manifest_path"],
            "tasks",
            "manifest path",
            suffix=".json",
        ),
        manifest_sha256=_expect_match(
            item["manifest_sha256"],
            _DIGEST_RE,
            "manifest digest",
        ),
        fixture_root=_expect_catalog_path(
            item["fixture_root"],
            "fixtures",
            "fixture root",
        ),
        grader_root=_expect_catalog_path(
            item["grader_root"],
            "graders",
            "grader root",
        ),
    )


def _validate_unique_paths(
    label: str,
    paths: list[str],
    *,
    reject_overlap: bool,
) -> None:
    folded = sorted(path.casefold() for path in paths)
    if len(set(folded)) != len(folded):
        raise EvaluationSuiteError(f"{label} contain duplicates")
    if reject_overlap and any(
        later.startswith(earlier + "/")
        for index, earlier in enumerate(folded)
        for later in folded[index + 1 :]
    ):
        raise EvaluationSuiteError(f"{label} overlap")


def parse_evaluation_suite_catalog(
    content: bytes | str,
) -> EvaluationSuiteCatalog:
    """Parse exact canonical catalog bytes without reading referenced files."""
    if isinstance(content, bytes):
        raw = content
    elif isinstance(content, str):
        try:
            raw = content.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise EvaluationSuiteError("catalog is not valid UTF-8") from exc
    else:
        raise EvaluationSuiteError("catalog content type is invalid")
    if not raw or len(raw) > MAX_CATALOG_BYTES:
        raise EvaluationSuiteError("catalog size is invalid")

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise EvaluationSuiteError("catalog is malformed") from exc

    data = _expect_object(value, TOP_LEVEL_KEYS, "catalog")
    canonical = json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    if raw != canonical:
        raise EvaluationSuiteError("catalog is not canonical JSON")
    if (
        type(data["schema_version"]) is not int
        or data["schema_version"] != SCHEMA_VERSION
    ):
        raise EvaluationSuiteError("catalog schema version is unsupported")

    task_values = data["tasks"]
    if not isinstance(task_values, list) or not 1 <= len(task_values) <= MAX_TASKS:
        raise EvaluationSuiteError("catalog tasks are invalid")
    entries = tuple(_parse_task_entry(item) for item in task_values)
    declared_count = _expect_integer(
        data["task_count"],
        "task count",
        1,
        MAX_TASKS,
    )
    if declared_count != len(entries):
        raise EvaluationSuiteError("task count does not match tasks")

    identities = tuple((entry.task_id, entry.task_version) for entry in entries)
    if identities != tuple(sorted(identities)) or len(set(identities)) != len(
        identities
    ):
        raise EvaluationSuiteError("tasks are unsorted or duplicated")

    _validate_unique_paths(
        "manifest paths",
        [entry.manifest_path for entry in entries],
        reject_overlap=False,
    )
    _validate_unique_paths(
        "fixture roots",
        [entry.fixture_root for entry in entries],
        reject_overlap=True,
    )
    _validate_unique_paths(
        "grader roots",
        [entry.grader_root for entry in entries],
        reject_overlap=True,
    )

    return EvaluationSuiteCatalog(
        suite_id=_expect_match(data["suite_id"], _ID_RE, "suite ID"),
        suite_version=_expect_integer(
            data["suite_version"],
            "suite version",
            1,
            1_000_000_000,
        ),
        foundation_sha=_expect_match(
            data["foundation_sha"],
            _SHA_RE,
            "Foundation SHA",
        ),
        tasks=entries,
        catalog_sha256=hashlib.sha256(raw).hexdigest(),
    )


def _stat_identity(info: os.stat_result) -> tuple[Any, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        stat.S_IMODE(info.st_mode),
        info.st_nlink,
        getattr(info, "st_mtime_ns", None),
        getattr(info, "st_ctime_ns", None),
    )


def _resolve_directory(value: str | os.PathLike[str], label: str) -> Path:
    path = Path(value)
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise EvaluationSuiteError(f"{label} is missing") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise EvaluationSuiteError(f"{label} is not a real directory")
    return resolved


def _resolve_below(
    root: Path,
    relative: str,
    *,
    kind: str,
    label: str,
) -> Path:
    current = root
    try:
        for part in relative.split("/"):
            current /= part
            if stat.S_ISLNK(current.lstat().st_mode):
                raise EvaluationSuiteError(f"{label} contains a symlink")
        resolved = current.resolve(strict=True)
        resolved.relative_to(root)
        info = resolved.stat()
    except (OSError, ValueError) as exc:
        raise EvaluationSuiteError(f"{label} is invalid") from exc

    if kind == "file" and not stat.S_ISREG(info.st_mode):
        raise EvaluationSuiteError(f"{label} is not a regular file")
    if kind == "directory" and not stat.S_ISDIR(info.st_mode):
        raise EvaluationSuiteError(f"{label} is not a directory")
    return resolved


def _hash_file(path: Path, expected: os.stat_result) -> tuple[str, bool]:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or _stat_identity(before) != _stat_identity(expected)
        ):
            raise EvaluationSuiteError("file changed before inspection")

        digest = hashlib.sha256()
        total = 0
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = None
            while chunk := handle.read(1_048_576):
                total += len(chunk)
                if total > MAX_FILE_BYTES:
                    raise EvaluationSuiteError("file exceeds the per-file limit")
                digest.update(chunk)
            after = os.fstat(handle.fileno())

        if total != before.st_size or _stat_identity(after) != _stat_identity(before):
            raise EvaluationSuiteError("file changed during inspection")
        return digest.hexdigest(), bool(before.st_mode & 0o111)
    except EvaluationSuiteError:
        raise
    except OSError as exc:
        raise EvaluationSuiteError("file cannot be read safely") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _read_regular_file(path: Path, maximum: int, label: str) -> bytes:
    descriptor: int | None = None
    try:
        expected = path.lstat()
        if (
            not stat.S_ISREG(expected.st_mode)
            or expected.st_nlink != 1
            or not 1 <= expected.st_size <= maximum
        ):
            raise EvaluationSuiteError(f"{label} is not a bounded regular file")
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if _stat_identity(before) != _stat_identity(expected):
            raise EvaluationSuiteError(f"{label} changed before reading")
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = None
            content = handle.read(maximum + 1)
            after = os.fstat(handle.fileno())
        if (
            len(content) != before.st_size
            or len(content) > maximum
            or _stat_identity(after) != _stat_identity(before)
        ):
            raise EvaluationSuiteError(f"{label} changed while reading")
        return content
    except EvaluationSuiteError:
        raise
    except OSError as exc:
        raise EvaluationSuiteError(f"{label} cannot be read safely") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def inspect_directory_bundle(value: str | os.PathLike[str]) -> DirectoryBundle:
    """Hash a local directory without importing or executing its contents."""
    root = _resolve_directory(value, "bundle root")
    root_device = root.stat().st_dev
    files: list[BundleFile] = []
    seen_paths: set[str] = set()
    total_bytes = 0

    def visit(directory: Path, expected: os.stat_result | None = None) -> None:
        nonlocal total_bytes
        try:
            before = directory.lstat()
            if (
                stat.S_ISLNK(before.st_mode)
                or not stat.S_ISDIR(before.st_mode)
                or before.st_dev != root_device
                or (
                    expected is not None
                    and _stat_identity(before) != _stat_identity(expected)
                )
            ):
                raise EvaluationSuiteError("directory changed before inspection")
            with os.scandir(directory) as iterator:
                children = sorted(iterator, key=lambda child: child.name)
        except EvaluationSuiteError:
            raise
        except OSError as exc:
            raise EvaluationSuiteError("directory cannot be read safely") from exc

        for child in children:
            path = Path(child.path)
            try:
                info = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise EvaluationSuiteError("bundle entry cannot be inspected") from exc
            if stat.S_ISLNK(info.st_mode) or info.st_dev != root_device:
                raise EvaluationSuiteError("bundle contains a link or mount escape")

            relative = path.relative_to(root).as_posix()
            folded = relative.casefold()
            if folded in seen_paths:
                raise EvaluationSuiteError("bundle contains case-ambiguous paths")
            seen_paths.add(folded)

            if stat.S_ISDIR(info.st_mode):
                visit(path, info)
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise EvaluationSuiteError("bundle contains a non-regular or hard-linked file")
            if info.st_size < 0 or info.st_size > MAX_FILE_BYTES:
                raise EvaluationSuiteError("bundle file size is invalid")

            total_bytes += info.st_size
            if total_bytes > MAX_BUNDLE_BYTES or len(files) >= MAX_BUNDLE_FILES:
                raise EvaluationSuiteError("bundle exceeds its bounded limits")
            sha256, executable = _hash_file(path, info)
            files.append(
                BundleFile(
                    path=relative,
                    size=info.st_size,
                    sha256=sha256,
                    executable=executable,
                )
            )

        try:
            after = directory.lstat()
        except OSError as exc:
            raise EvaluationSuiteError("directory changed during inspection") from exc
        if _stat_identity(after) != _stat_identity(before):
            raise EvaluationSuiteError("directory changed during inspection")

    visit(root)
    if not files:
        raise EvaluationSuiteError("bundle contains no regular files")
    files.sort(key=lambda item: item.path)
    index = {
        "files": [
            {
                "executable": item.executable,
                "path": item.path,
                "sha256": item.sha256,
                "size": item.size,
            }
            for item in files
        ],
        "schema_version": BUNDLE_INDEX_VERSION,
    }
    encoded = json.dumps(
        index,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return DirectoryBundle(
        sha256=hashlib.sha256(encoded).hexdigest(),
        files=tuple(files),
        uncompressed_bytes=total_bytes,
    )


def load_evaluation_suite(
    content: bytes | str,
    suite_root: str | os.PathLike[str],
) -> EvaluationSuite:
    """Validate catalog, manifests, bundles, and entrypoints without execution."""
    catalog = parse_evaluation_suite_catalog(content)
    root = _resolve_directory(suite_root, "suite root")
    validated_tasks: list[ValidatedSuiteTask] = []

    for entry in catalog.tasks:
        manifest_path = _resolve_below(
            root,
            entry.manifest_path,
            kind="file",
            label="task manifest",
        )
        raw_manifest = _read_regular_file(
            manifest_path,
            MAX_MANIFEST_BYTES,
            "task manifest",
        )
        if hashlib.sha256(raw_manifest).hexdigest() != entry.manifest_sha256:
            raise EvaluationSuiteError("task manifest digest does not match catalog")
        try:
            manifest = parse_evaluation_task(raw_manifest)
        except EvaluationTaskError as exc:
            raise EvaluationSuiteError("task manifest is invalid") from exc
        if (
            manifest.task_id,
            manifest.task_version,
            manifest.manifest_sha256,
        ) != (
            entry.task_id,
            entry.task_version,
            entry.manifest_sha256,
        ):
            raise EvaluationSuiteError("task manifest identity does not match catalog")

        fixture_root = _resolve_below(
            root,
            entry.fixture_root,
            kind="directory",
            label="fixture root",
        )
        grader_root = _resolve_below(
            root,
            entry.grader_root,
            kind="directory",
            label="grader root",
        )
        fixture_bundle = inspect_directory_bundle(fixture_root)
        grader_bundle = inspect_directory_bundle(grader_root)

        expected_fixture = manifest.fixture_bundle
        if (
            fixture_bundle.sha256,
            fixture_bundle.file_count,
            fixture_bundle.uncompressed_bytes,
        ) != (
            expected_fixture.sha256,
            expected_fixture.file_count,
            expected_fixture.uncompressed_bytes,
        ):
            raise EvaluationSuiteError("fixture bundle identity does not match manifest")
        if grader_bundle.sha256 != manifest.grader.sha256:
            raise EvaluationSuiteError("grader bundle identity does not match manifest")
        if manifest.grader.entrypoint not in {
            item.path for item in grader_bundle.files
        }:
            raise EvaluationSuiteError("grader entrypoint is missing from grader bundle")

        validated_tasks.append(
            ValidatedSuiteTask(
                entry=entry,
                manifest=manifest,
                fixture_bundle=fixture_bundle,
                grader_bundle=grader_bundle,
            )
        )

    return EvaluationSuite(catalog=catalog, tasks=tuple(validated_tasks))

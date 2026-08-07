#!/usr/bin/env python3
"""Materialize exactly one validated public fixture into a new trial workspace."""
from __future__ import annotations

import hashlib
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

from scripts.agent_eval_suite_contract import EvaluationSuite, ValidatedSuiteTask, inspect_directory_bundle
from scripts.agent_eval_trial_request import AgentFixtureFile, AgentTrialRequest, agent_trial_request_sha256

MAX_SOURCE_FILE_BYTES = 1_073_741_824


class AgentTrialWorkspaceError(ValueError):
    """A fixture cannot be safely reproduced inside the requested workspace."""


@dataclass(frozen=True)
class MaterializedTrialWorkspace:
    destination: str
    request_sha256: str
    fixture_sha256: str
    file_count: int
    uncompressed_bytes: int


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


def _real_directory(path: Path, label: str) -> Path:
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise AgentTrialWorkspaceError(f"{label} is missing") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise AgentTrialWorkspaceError(f"{label} is not a real directory")
    return resolved


def _relative_parts(value: str) -> tuple[str, ...]:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith(("/", "\\", "./", "../"))
        or "\\" in value
        or "//" in value
        or "\x00" in value
    ):
        raise AgentTrialWorkspaceError("fixture path is unsafe")
    parts = tuple(value.split("/"))
    if any(part in {"", ".", ".."} or part.casefold() == ".git" for part in parts):
        raise AgentTrialWorkspaceError("fixture path is unsafe")
    if len(parts[0]) >= 2 and parts[0][1:2] == ":":
        raise AgentTrialWorkspaceError("fixture path is unsafe")
    return parts


def _suite_task(suite: EvaluationSuite, task_id: str) -> ValidatedSuiteTask:
    matches = [task for task in suite.tasks if task.entry.task_id == task_id]
    if len(matches) != 1:
        raise AgentTrialWorkspaceError("request task does not resolve exactly once in the suite")
    return matches[0]


def _validate_request_suite(request: AgentTrialRequest, suite: EvaluationSuite) -> ValidatedSuiteTask:
    if (
        request.suite_id,
        request.suite_version,
        request.catalog_sha256,
        request.foundation_sha,
    ) != (
        suite.catalog.suite_id,
        suite.catalog.suite_version,
        suite.catalog.catalog_sha256,
        suite.catalog.foundation_sha,
    ):
        raise AgentTrialWorkspaceError("request suite identity does not match the loaded suite")
    task = _suite_task(suite, request.task_id)
    if (
        request.task_version,
        request.manifest_sha256,
        request.fixture_bundle.sha256,
        request.fixture_bundle.file_count,
        request.fixture_bundle.uncompressed_bytes,
    ) != (
        task.entry.task_version,
        task.entry.manifest_sha256,
        task.fixture_bundle.sha256,
        task.fixture_bundle.file_count,
        task.fixture_bundle.uncompressed_bytes,
    ):
        raise AgentTrialWorkspaceError("request task or fixture identity does not match the loaded suite")
    request_files = tuple(
        (item.path, item.size, item.sha256, item.executable)
        for item in request.fixture_bundle.files
    )
    suite_files = tuple(
        (item.path, item.size, item.sha256, item.executable)
        for item in task.fixture_bundle.files
    )
    if request_files != suite_files:
        raise AgentTrialWorkspaceError("request fixture file index does not match the loaded suite")
    folded = tuple(item.path.casefold() for item in request.fixture_bundle.files)
    if len(set(folded)) != len(folded):
        raise AgentTrialWorkspaceError("request fixture file index is case-ambiguous")
    return task


def _source_root(suite_root: Path, relative: str) -> Path:
    root = _real_directory(suite_root, "suite root")
    current = root
    try:
        for part in _relative_parts(relative):
            current /= part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise AgentTrialWorkspaceError("fixture root contains a symlink")
        resolved = current.resolve(strict=True)
        resolved.relative_to(root)
    except AgentTrialWorkspaceError:
        raise
    except (OSError, ValueError) as exc:
        raise AgentTrialWorkspaceError("fixture root is unsafe") from exc
    if not resolved.is_dir():
        raise AgentTrialWorkspaceError("fixture root is not a directory")
    return resolved


def _read_source_file(root: Path, item: AgentFixtureFile) -> bytes:
    parts = _relative_parts(item.path)
    root_device = root.stat().st_dev
    current = root
    try:
        for part in parts[:-1]:
            current /= part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_dev != root_device:
                raise AgentTrialWorkspaceError("fixture path traverses an unsafe directory")
        path = current / parts[-1]
        expected = path.lstat()
    except AgentTrialWorkspaceError:
        raise
    except OSError as exc:
        raise AgentTrialWorkspaceError("fixture source file is missing") from exc
    if (
        stat.S_ISLNK(expected.st_mode)
        or not stat.S_ISREG(expected.st_mode)
        or expected.st_nlink != 1
        or expected.st_dev != root_device
        or expected.st_size != item.size
        or expected.st_size > MAX_SOURCE_FILE_BYTES
        or bool(expected.st_mode & 0o111) != item.executable
    ):
        raise AgentTrialWorkspaceError("fixture source file metadata does not match the request")

    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if _stat_identity(before) != _stat_identity(expected):
            raise AgentTrialWorkspaceError("fixture source file changed before reading")
        chunks: list[bytes] = []
        total = 0
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1_048_576)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_SOURCE_FILE_BYTES or total > item.size:
                raise AgentTrialWorkspaceError("fixture source file exceeds its bounded size")
            digest.update(chunk)
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if total != item.size or _stat_identity(after) != _stat_identity(before):
            raise AgentTrialWorkspaceError("fixture source file changed while reading")
        if digest.hexdigest() != item.sha256:
            raise AgentTrialWorkspaceError("fixture source file digest does not match the request")
        return b"".join(chunks)
    except AgentTrialWorkspaceError:
        raise
    except OSError as exc:
        raise AgentTrialWorkspaceError("fixture source file cannot be read safely") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _prepare_destination(destination: Path) -> Path:
    if destination.exists() or destination.is_symlink():
        raise AgentTrialWorkspaceError("trial workspace destination already exists")
    parent = _real_directory(destination.parent, "trial workspace parent")
    target = parent / destination.name
    if target != destination.resolve(strict=False):
        raise AgentTrialWorkspaceError("trial workspace destination is not a direct safe child")
    try:
        target.mkdir(mode=0o700)
    except OSError as exc:
        raise AgentTrialWorkspaceError("trial workspace destination cannot be created") from exc
    return target


def _write_destination_file(root: Path, item: AgentFixtureFile, content: bytes, sequence: int) -> None:
    parts = _relative_parts(item.path)
    directory = root
    try:
        for part in parts[:-1]:
            directory /= part
            if not directory.exists():
                directory.mkdir(mode=0o755)
            info = directory.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise AgentTrialWorkspaceError("trial workspace directory became unsafe")
        destination = directory / parts[-1]
        if destination.exists() or destination.is_symlink():
            raise AgentTrialWorkspaceError("trial workspace file already exists")
        temporary = directory / f".{parts[-1]}.tmp-{sequence}"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            view = memoryview(content)
            written = 0
            while written < len(view):
                written += os.write(descriptor, view[written:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.chmod(temporary, 0o755 if item.executable else 0o644)
        os.replace(temporary, destination)
    except AgentTrialWorkspaceError:
        raise
    except OSError as exc:
        raise AgentTrialWorkspaceError("trial workspace file cannot be written safely") from exc


def materialize_agent_trial_workspace(
    request: AgentTrialRequest,
    suite: EvaluationSuite,
    suite_root: str | os.PathLike[str],
    destination: str | os.PathLike[str],
) -> MaterializedTrialWorkspace:
    """Copy only the sealed selected fixture into a newly created disposable directory."""
    task = _validate_request_suite(request, suite)
    source = _source_root(Path(suite_root), task.entry.fixture_root)
    source_bundle = inspect_directory_bundle(source)
    expected_index = tuple(
        (item.path, item.size, item.sha256, item.executable)
        for item in request.fixture_bundle.files
    )
    observed_index = tuple(
        (item.path, item.size, item.sha256, item.executable)
        for item in source_bundle.files
    )
    if (
        source_bundle.sha256,
        source_bundle.file_count,
        source_bundle.uncompressed_bytes,
        observed_index,
    ) != (
        request.fixture_bundle.sha256,
        request.fixture_bundle.file_count,
        request.fixture_bundle.uncompressed_bytes,
        expected_index,
    ):
        raise AgentTrialWorkspaceError("source fixture no longer matches the sealed request")

    target = _prepare_destination(Path(destination))
    try:
        for sequence, item in enumerate(request.fixture_bundle.files, start=1):
            content = _read_source_file(source, item)
            _write_destination_file(target, item, content, sequence)
        materialized = inspect_directory_bundle(target)
        final_index = tuple(
            (item.path, item.size, item.sha256, item.executable)
            for item in materialized.files
        )
        if (
            materialized.sha256,
            materialized.file_count,
            materialized.uncompressed_bytes,
            final_index,
        ) != (
            request.fixture_bundle.sha256,
            request.fixture_bundle.file_count,
            request.fixture_bundle.uncompressed_bytes,
            expected_index,
        ):
            raise AgentTrialWorkspaceError("materialized trial workspace does not match the sealed fixture")
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise

    return MaterializedTrialWorkspace(
        destination=str(target),
        request_sha256=agent_trial_request_sha256(request),
        fixture_sha256=materialized.sha256,
        file_count=materialized.file_count,
        uncompressed_bytes=materialized.uncompressed_bytes,
    )

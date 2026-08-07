#!/usr/bin/env python3
"""Bind one sealed disposable trial workspace to deterministic local Git SHAs."""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from scripts.agent_eval_trial_delta import AgentTrialDelta, inspect_agent_trial_delta
from scripts.agent_eval_trial_request import AgentTrialRequest, agent_trial_request_sha256

COMMAND_TIMEOUT_SECONDS = 30
MAX_COMMAND_OUTPUT_BYTES = 65_536
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
BASELINE_DATE = "2000-01-01T00:00:00Z"
CANDIDATE_DATE = "2000-01-01T00:00:01Z"
INTERNAL_NAME = "Foundation Evaluation"
INTERNAL_EMAIL = "evaluation@invalid.local"


class AgentTrialGitError(ValueError):
    """Trial Git identity cannot be created or verified safely."""


@dataclass(frozen=True)
class InitializedTrialGit:
    request_sha256: str
    workspace: str
    metadata_dir: str
    base_sha: str
    baseline_tree_sha: str
    baseline_bundle_sha256: str


@dataclass(frozen=True)
class FinalizedTrialGit:
    request_sha256: str
    workspace: str
    metadata_dir: str
    base_sha: str
    candidate_sha: str
    candidate_tree_sha: str
    candidate_bundle_sha256: str
    mutation_count: int
    scope_violation_count: int


def _real_directory(path: Path, label: str) -> Path:
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise AgentTrialGitError(f"{label} is missing") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise AgentTrialGitError(f"{label} is not a real directory")
    return resolved


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _validate_layout(workspace: Path, metadata_dir: Path) -> tuple[Path, Path]:
    workspace = _real_directory(workspace, "trial workspace")
    if any(part.casefold() == ".git" for part in workspace.parts):
        raise AgentTrialGitError("trial workspace is inside Git metadata")
    if metadata_dir.exists() or metadata_dir.is_symlink():
        raise AgentTrialGitError("trial Git metadata destination already exists")
    parent = _real_directory(metadata_dir.parent, "trial Git metadata parent")
    metadata = (parent / metadata_dir.name).resolve(strict=False)
    if any(part.casefold() == ".git" for part in metadata.parts):
        raise AgentTrialGitError("trial Git metadata path must not use .git")
    if metadata == workspace or _is_within(metadata, workspace) or _is_within(workspace, metadata):
        raise AgentTrialGitError("trial Git metadata and agent workspace must be disjoint")
    return workspace, metadata


def _git_executable() -> str:
    value = shutil.which("git")
    if not value:
        raise AgentTrialGitError("git executable is unavailable")
    try:
        path = Path(value).resolve(strict=True)
    except OSError as exc:
        raise AgentTrialGitError("git executable cannot be resolved") from exc
    if not path.is_file():
        raise AgentTrialGitError("git executable is invalid")
    return str(path)


def _git_environment(*, git_dir: Path | None = None, work_tree: Path | None = None, date: str | None = None) -> dict[str, str]:
    environment = {
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT", "WINDIR", "TMP", "TEMP", "TMPDIR")
        if key in os.environ
    }
    environment.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": INTERNAL_NAME,
        "GIT_AUTHOR_EMAIL": INTERNAL_EMAIL,
        "GIT_COMMITTER_NAME": INTERNAL_NAME,
        "GIT_COMMITTER_EMAIL": INTERNAL_EMAIL,
        "LC_ALL": "C",
        "LANG": "C",
    })
    if git_dir is not None:
        environment["GIT_DIR"] = str(git_dir)
        environment["GIT_INDEX_FILE"] = str(git_dir / "index")
    if work_tree is not None:
        environment["GIT_WORK_TREE"] = str(work_tree)
    if date is not None:
        environment["GIT_AUTHOR_DATE"] = date
        environment["GIT_COMMITTER_DATE"] = date
    return environment


def _run_git(
    executable: str,
    args: list[str],
    *,
    environment: dict[str, str],
    cwd: Path | None = None,
    input_bytes: bytes | None = None,
) -> bytes:
    try:
        completed = subprocess.run(
            [executable, *args],
            cwd=None if cwd is None else str(cwd),
            env=environment,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AgentTrialGitError("local git command could not complete") from exc
    if len(completed.stdout) > MAX_COMMAND_OUTPUT_BYTES or len(completed.stderr) > MAX_COMMAND_OUTPUT_BYTES:
        raise AgentTrialGitError("local git command diagnostics exceeded bounded limits")
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace")[:500]
        raise AgentTrialGitError(f"local git command failed: {detail}")
    return completed.stdout


def _sha(output: bytes, label: str) -> str:
    try:
        value = output.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise AgentTrialGitError(f"{label} is not ASCII") from exc
    if _SHA_RE.fullmatch(value) is None:
        raise AgentTrialGitError(f"{label} is invalid")
    return value


def _git_args() -> list[str]:
    return [
        "-c", "core.autocrlf=false",
        "-c", "core.filemode=true",
        "-c", "core.safecrlf=false",
    ]


def _stage_tree(executable: str, metadata: Path, workspace: Path, *, date: str) -> str:
    environment = _git_environment(git_dir=metadata, work_tree=workspace, date=date)
    common = _git_args()
    _run_git(executable, [*common, "add", "-f", "-A", "--", "."], environment=environment, cwd=workspace)
    return _sha(_run_git(executable, [*common, "write-tree"], environment=environment, cwd=workspace), "Git tree SHA")


def _commit_tree(
    executable: str,
    metadata: Path,
    tree_sha: str,
    *,
    parent_sha: str | None,
    date: str,
    message: bytes,
) -> str:
    args = [*_git_args(), "commit-tree", tree_sha]
    if parent_sha is not None:
        args.extend(["-p", parent_sha])
    return _sha(
        _run_git(
            executable,
            args,
            environment=_git_environment(git_dir=metadata, date=date),
            input_bytes=message,
        ),
        "Git commit SHA",
    )


def _update_ref(executable: str, metadata: Path, ref: str, sha: str) -> None:
    _run_git(
        executable,
        [*_git_args(), "update-ref", ref, sha],
        environment=_git_environment(git_dir=metadata),
    )


def _read_ref(executable: str, metadata: Path, ref: str) -> str:
    return _sha(
        _run_git(
            executable,
            [*_git_args(), "rev-parse", "--verify", ref],
            environment=_git_environment(git_dir=metadata),
        ),
        "Git ref SHA",
    )


def _assert_no_remote(executable: str, metadata: Path) -> None:
    output = _run_git(
        executable,
        [*_git_args(), "remote"],
        environment=_git_environment(git_dir=metadata),
    )
    if output.strip():
        raise AgentTrialGitError("trial Git metadata unexpectedly contains a remote")


def initialize_trial_git_identity(
    request: AgentTrialRequest,
    workspace: str | os.PathLike[str],
    metadata_dir: str | os.PathLike[str],
) -> InitializedTrialGit:
    """Create a deterministic root commit for the exact sealed fixture bytes."""
    workspace_path, metadata = _validate_layout(Path(workspace), Path(metadata_dir))
    delta = inspect_agent_trial_delta(request, workspace_path)
    if delta.mutation_count != 0 or delta.candidate_bundle_sha256 != request.fixture_bundle.sha256:
        raise AgentTrialGitError("baseline Git identity requires an unchanged sealed fixture workspace")
    executable = _git_executable()
    try:
        _run_git(
            executable,
            ["init", "--bare", "--quiet", "--object-format=sha1", str(metadata)],
            environment=_git_environment(),
        )
        _assert_no_remote(executable, metadata)
        tree_sha = _stage_tree(executable, metadata, workspace_path, date=BASELINE_DATE)
        base_sha = _commit_tree(
            executable,
            metadata,
            tree_sha,
            parent_sha=None,
            date=BASELINE_DATE,
            message=b"Foundation evaluation baseline\n",
        )
        _update_ref(executable, metadata, "refs/heads/baseline", base_sha)
        if _read_ref(executable, metadata, "refs/heads/baseline") != base_sha:
            raise AgentTrialGitError("baseline Git ref does not match the created commit")
    except Exception:
        shutil.rmtree(metadata, ignore_errors=True)
        raise
    return InitializedTrialGit(
        request_sha256=agent_trial_request_sha256(request),
        workspace=str(workspace_path),
        metadata_dir=str(metadata),
        base_sha=base_sha,
        baseline_tree_sha=tree_sha,
        baseline_bundle_sha256=delta.candidate_bundle_sha256,
    )


def _validate_initialized(request: AgentTrialRequest, initialized: InitializedTrialGit) -> tuple[str, Path, Path]:
    if not isinstance(initialized, InitializedTrialGit):
        raise AgentTrialGitError("initialized trial Git evidence is invalid")
    request_sha = agent_trial_request_sha256(request)
    if initialized.request_sha256 != request_sha or initialized.baseline_bundle_sha256 != request.fixture_bundle.sha256:
        raise AgentTrialGitError("initialized trial Git evidence does not match the sealed request")
    workspace = _real_directory(Path(initialized.workspace), "trial workspace")
    metadata = _real_directory(Path(initialized.metadata_dir), "trial Git metadata")
    if any(part.casefold() == ".git" for part in metadata.parts):
        raise AgentTrialGitError("trial Git metadata path is unsafe")
    if metadata == workspace or _is_within(metadata, workspace) or _is_within(workspace, metadata):
        raise AgentTrialGitError("trial Git metadata and workspace are no longer disjoint")
    executable = _git_executable()
    _assert_no_remote(executable, metadata)
    if _read_ref(executable, metadata, "refs/heads/baseline") != initialized.base_sha:
        raise AgentTrialGitError("baseline Git ref moved after initialization")
    tree = _sha(
        _run_git(
            executable,
            [*_git_args(), "rev-parse", f"{initialized.base_sha}^{{tree}}"],
            environment=_git_environment(git_dir=metadata),
        ),
        "baseline tree SHA",
    )
    if tree != initialized.baseline_tree_sha:
        raise AgentTrialGitError("baseline Git tree does not match initialization evidence")
    return executable, workspace, metadata


def finalize_trial_git_identity(
    request: AgentTrialRequest,
    initialized: InitializedTrialGit,
) -> FinalizedTrialGit:
    """Create a deterministic candidate child commit for the final safe workspace state."""
    executable, workspace, metadata = _validate_initialized(request, initialized)
    before: AgentTrialDelta = inspect_agent_trial_delta(request, workspace)
    tree_sha = _stage_tree(executable, metadata, workspace, date=CANDIDATE_DATE)
    candidate_sha = _commit_tree(
        executable,
        metadata,
        tree_sha,
        parent_sha=initialized.base_sha,
        date=CANDIDATE_DATE,
        message=b"Foundation evaluation candidate\n",
    )
    _update_ref(executable, metadata, "refs/heads/candidate", candidate_sha)
    if _read_ref(executable, metadata, "refs/heads/candidate") != candidate_sha:
        raise AgentTrialGitError("candidate Git ref does not match the created commit")
    parent = _sha(
        _run_git(
            executable,
            [*_git_args(), "rev-parse", f"{candidate_sha}^"],
            environment=_git_environment(git_dir=metadata),
        ),
        "candidate parent SHA",
    )
    if parent != initialized.base_sha:
        raise AgentTrialGitError("candidate Git commit parent does not match the baseline")
    committed_tree = _sha(
        _run_git(
            executable,
            [*_git_args(), "rev-parse", f"{candidate_sha}^{{tree}}"],
            environment=_git_environment(git_dir=metadata),
        ),
        "candidate tree SHA",
    )
    if committed_tree != tree_sha:
        raise AgentTrialGitError("candidate Git tree identity changed during commit creation")
    after = inspect_agent_trial_delta(request, workspace)
    if after != before:
        raise AgentTrialGitError("candidate workspace changed while Git identity was being created")
    return FinalizedTrialGit(
        request_sha256=initialized.request_sha256,
        workspace=str(workspace),
        metadata_dir=str(metadata),
        base_sha=initialized.base_sha,
        candidate_sha=candidate_sha,
        candidate_tree_sha=tree_sha,
        candidate_bundle_sha256=before.candidate_bundle_sha256,
        mutation_count=before.mutation_count,
        scope_violation_count=before.scope_violation_count,
    )

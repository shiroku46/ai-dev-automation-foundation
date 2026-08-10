"""Detached exact-SHA repository map/context regression tests."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.agent_repository_context import (
    RepositoryContextError,
    build_repository_context_package,
)
from scripts.agent_repository_detached import (
    DetachedRepositoryError,
    build_detached_repository_context_package,
    build_detached_repository_map,
)
from scripts.agent_repository_map import build_repository_map


class DetachedRepositoryTest(unittest.TestCase):
    def _run(self, command: list[str], *, cwd: Path | None = None) -> str:
        completed = subprocess.run(
            command,
            cwd=None if cwd is None else str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        return completed.stdout.strip()

    def _git(self, repo: Path, *args: str) -> str:
        return self._run(["git", "-C", str(repo), *args])

    def _git_dir(self, metadata: Path, *args: str) -> str:
        return self._run(["git", f"--git-dir={metadata}", *args])

    def _make_repository(
        self,
        root: Path,
        *,
        malformed_python: bool = False,
        sensitive_file: bool = False,
        tracked_symlink: bool = False,
    ) -> tuple[Path, str, str]:
        repo = root / "ordinary"
        repo.mkdir()
        self._run(["git", "init", "-q", "-b", "main", str(repo)])
        self._git(repo, "config", "user.name", "Foundation Test")
        self._git(repo, "config", "user.email", "foundation-test@invalid.local")
        (repo / "pkg").mkdir()
        (repo / "tests").mkdir()
        (repo / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (repo / "pkg" / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
        (repo / "pkg" / "core.py").write_text(
            "from pkg import helper\n\ndef answer():\n    return helper.VALUE\n",
            encoding="utf-8",
        )
        (repo / "tests" / "test_core.py").write_text(
            "from pkg import core\n\ndef test_answer():\n    assert core.answer() == 1\n",
            encoding="utf-8",
        )
        if malformed_python:
            (repo / "broken.py").write_text("def broken(:\n", encoding="utf-8")
        if sensitive_file:
            sensitive = "api_" + "key = " + ("x" * 32) + "\n"
            (repo / "sensitive.txt").write_text(sensitive, encoding="utf-8")
        if tracked_symlink:
            os.symlink("pkg/helper.py", repo / "linked.py")
        (repo / "README.md").write_text("first\n", encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-q", "-m", "baseline")
        parent = self._git(repo, "rev-parse", "HEAD")
        (repo / "README.md").write_text("second\n", encoding="utf-8")
        self._git(repo, "add", "README.md")
        self._git(repo, "commit", "-q", "-m", "current")
        head = self._git(repo, "rev-parse", "HEAD")
        return repo, head, parent

    def _detached_copy(self, root: Path, repo: Path) -> tuple[Path, Path]:
        workspace = root / "workspace"
        shutil.copytree(repo, workspace, ignore=shutil.ignore_patterns(".git"))
        metadata = root / "metadata-bare"
        self._run(["git", "clone", "--bare", "-q", str(repo), str(metadata)])
        self._git_dir(metadata, "remote", "remove", "origin")
        return workspace, metadata

    def _metadata_snapshot(self, metadata: Path) -> tuple[tuple[str, str], ...]:
        records: list[tuple[str, str]] = []
        for path in sorted(metadata.rglob("*")):
            if path.is_file() and not path.is_symlink():
                records.append(
                    (
                        path.relative_to(metadata).as_posix(),
                        hashlib.sha256(path.read_bytes()).hexdigest(),
                    )
                )
        return tuple(records)

    def test_detached_map_and_context_match_ordinary_and_ignore_workspace_dirt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, head, _ = self._make_repository(root)
            workspace, metadata = self._detached_copy(root, repo)
            before = self._metadata_snapshot(metadata)

            ordinary_map = build_repository_map(repo, head)
            detached_map = build_detached_repository_map(workspace, metadata, head)
            self.assertEqual(detached_map, ordinary_map)

            ordinary_context = build_repository_context_package(
                repo,
                ordinary_map,
                ("pkg/core.py",),
                ("pkg/core.py",),
                max_depth=2,
                max_paths=32,
            )
            detached_context = build_detached_repository_context_package(
                workspace,
                metadata,
                detached_map,
                ("pkg/core.py",),
                ("pkg/core.py",),
                max_depth=2,
                max_paths=32,
            )
            self.assertEqual(detached_context, ordinary_context)

            (workspace / "pkg" / "helper.py").write_text("VALUE = 999\n", encoding="utf-8")
            (workspace / "scratch.txt").write_text("untracked\n", encoding="utf-8")
            self.assertEqual(build_detached_repository_map(workspace, metadata, head), ordinary_map)
            self.assertEqual(
                build_detached_repository_context_package(
                    workspace,
                    metadata,
                    detached_map,
                    ("pkg/core.py",),
                    ("pkg/core.py",),
                    max_depth=2,
                    max_paths=32,
                ),
                ordinary_context,
            )
            self.assertFalse((workspace / ".git").exists())
            self.assertEqual(self._metadata_snapshot(metadata), before)

    def test_remote_ref_layout_nonbare_and_symlink_boundaries_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, head, parent = self._make_repository(root)
            workspace, metadata = self._detached_copy(root, repo)

            self._git_dir(metadata, "remote", "add", "blocked", "https://example.invalid/repository")
            with self.assertRaises(DetachedRepositoryError):
                build_detached_repository_map(workspace, metadata, head)
            self._git_dir(metadata, "remote", "remove", "blocked")

            with self.assertRaises(DetachedRepositoryError):
                build_detached_repository_map(workspace, metadata, parent)

            head_ref = self._git_dir(metadata, "symbolic-ref", "HEAD")
            self._git_dir(metadata, "update-ref", head_ref, parent)
            with self.assertRaises(DetachedRepositoryError):
                build_detached_repository_map(workspace, metadata, head)
            self._git_dir(metadata, "update-ref", head_ref, head)

            nonbare_work = root / "nonbare-work"
            nonbare_metadata = root / "nonbare-metadata"
            self._run(
                [
                    "git",
                    "init",
                    "-q",
                    f"--separate-git-dir={nonbare_metadata}",
                    str(nonbare_work),
                ]
            )
            with self.assertRaises(DetachedRepositoryError):
                build_detached_repository_map(workspace, nonbare_metadata, head)

            nested_metadata = workspace / "nested-metadata"
            shutil.copytree(metadata, nested_metadata)
            with self.assertRaises(DetachedRepositoryError):
                build_detached_repository_map(workspace, nested_metadata, head)

            if hasattr(os, "symlink"):
                metadata_link = root / "metadata-link"
                try:
                    os.symlink(metadata, metadata_link, target_is_directory=True)
                except (OSError, NotImplementedError):
                    pass
                else:
                    with self.assertRaises(DetachedRepositoryError):
                        build_detached_repository_map(workspace, metadata_link, head)

    def test_malformed_python_sensitive_context_and_invalid_scope_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            malformed_root = Path(temp) / "malformed"
            malformed_root.mkdir()
            repo, head, _ = self._make_repository(malformed_root, malformed_python=True)
            workspace, metadata = self._detached_copy(malformed_root, repo)
            with self.assertRaises(DetachedRepositoryError):
                build_detached_repository_map(workspace, metadata, head)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, head, _ = self._make_repository(root, sensitive_file=True)
            workspace, metadata = self._detached_copy(root, repo)
            repository_map = build_detached_repository_map(workspace, metadata, head)
            with self.assertRaises(RepositoryContextError):
                build_detached_repository_context_package(
                    workspace,
                    metadata,
                    repository_map,
                    ("sensitive.txt",),
                    ("pkg/core.py",),
                )
            with self.assertRaises(RepositoryContextError):
                build_detached_repository_context_package(
                    workspace,
                    metadata,
                    repository_map,
                    ("pkg/core.py",),
                    ("pkg/core.py", "README.md"),
                )

    @unittest.skipIf(os.name == "nt", "tracked symlink fixture requires POSIX symlink semantics")
    def test_tracked_symlink_retains_e1_fail_closed_semantics(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, head, _ = self._make_repository(root, tracked_symlink=True)
            workspace, metadata = self._detached_copy(root, repo)
            with self.assertRaises(DetachedRepositoryError):
                build_detached_repository_map(workspace, metadata, head)

    def test_detached_tracked_file_bound_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, head, _ = self._make_repository(root)
            workspace, metadata = self._detached_copy(root, repo)
            with mock.patch("scripts.agent_repository_detached.MAX_TRACKED_FILES", 1):
                with self.assertRaises(DetachedRepositoryError):
                    build_detached_repository_map(workspace, metadata, head)


if __name__ == "__main__":
    unittest.main()

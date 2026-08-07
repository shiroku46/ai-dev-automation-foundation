"""Regression tests for sealed map-selected repository read context."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from scripts.agent_repository_context import (
    RepositoryContextError,
    build_repository_context_package,
    serialize_repository_context_package,
)
from scripts.agent_repository_map import build_repository_map


def git(repo: Path, *args: str) -> str:
    executable = shutil.which("git")
    if executable is None:
        raise unittest.SkipTest("git is unavailable")
    environment = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "Repository Context Test",
        "GIT_AUTHOR_EMAIL": "repository-context@invalid.local",
        "GIT_COMMITTER_NAME": "Repository Context Test",
        "GIT_COMMITTER_EMAIL": "repository-context@invalid.local",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
        "LC_ALL": "C",
        "LANG": "C",
    }
    completed = subprocess.run(
        [executable, "-C", str(repo), *args], env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return completed.stdout.strip()


def write(repo: Path, relative: str, content: str | bytes) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def graph_files() -> dict[str, str]:
    return {
        "pkg/__init__.py": "\n",
        "pkg/core.py": "def normalize(value):\n    return value.strip()\n",
        "pkg/helper.py": "def helper():\n    return 'helper'\n",
        "pkg/sub.py": "from . import helper\nfrom .core import normalize\n\ndef value(text):\n    return helper.helper() + normalize(text)\n",
        "app.py": "import pkg.sub\nfrom pkg import helper\n\ndef run(value):\n    return pkg.sub.value(value) + helper.helper()\n",
        "tests/test_app.py": "from app import run\n\ndef test_run():\n    assert run(' x ')\n",
    }


def init_repo(root: Path, files: dict[str, str | bytes]) -> tuple[Path, str]:
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet", "--object-format=sha1")
    for relative, content in files.items():
        write(repo, relative, content)
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", "fixture")
    return repo, git(repo, "rev-parse", "HEAD")


class RepositoryContextPackageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("git") is None:
            raise unittest.SkipTest("git is unavailable")

    def build(self, repo: Path, sha: str, *, seeds=("pkg/core.py",), scope=("pkg/core.py",), depth=3, max_paths=64):
        repository_map = build_repository_map(repo, sha)
        return build_repository_context_package(
            repo,
            repository_map,
            seeds,
            scope,
            max_depth=depth,
            max_paths=max_paths,
        )

    def test_dirty_and_untracked_worktree_bytes_do_not_change_package(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, sha = init_repo(Path(temp), graph_files())
            first = self.build(repo, sha)
            write(repo, "pkg/core.py", "this is not the committed content\n")
            write(repo, "untracked.txt", "untracked\n")
            second = self.build(repo, sha)
            self.assertEqual(first, second)
            self.assertEqual(serialize_repository_context_package(first), serialize_repository_context_package(second))
            core = next(item for item in second.files if item.path == "pkg/core.py")
            self.assertIn("return value.strip()", core.content)
            self.assertNotIn("untracked.txt", second.read_paths)

    def test_map_selected_dependencies_dependents_and_tests_are_included_deterministically(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, sha = init_repo(Path(temp), graph_files())
            package = self.build(repo, sha)
            self.assertEqual(package.seed_paths, ("pkg/core.py",))
            self.assertIn("pkg/sub.py", package.dependent_paths)
            self.assertIn("app.py", package.dependent_paths)
            self.assertIn("tests/test_app.py", package.dependent_paths)
            self.assertEqual(package.test_paths, ("tests/test_app.py",))
            self.assertEqual(package.read_paths, tuple(sorted(package.read_paths)))
            self.assertEqual({item.path for item in package.files}, set(package.seed_paths) | set(package.context_paths) | set(package.dependent_paths))

    def test_read_context_can_be_outside_write_scope_without_widening_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, sha = init_repo(Path(temp), graph_files())
            package = self.build(repo, sha, scope=("pkg/core.py",))
            self.assertEqual(package.trusted_allowed_paths, ("pkg/core.py",))
            self.assertIn("app.py", package.read_paths)
            self.assertNotIn("app.py", package.trusted_allowed_paths)
            self.assertFalse(hasattr(package, "expanded_allowed_paths"))
            self.assertFalse(hasattr(package, "writable_context"))

    def test_exact_repository_and_map_identity_mismatches_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, sha = init_repo(Path(temp), graph_files())
            repository_map = build_repository_map(repo, sha)
            write(repo, "new.py", "value = 1\n")
            git(repo, "add", "-A")
            git(repo, "commit", "--quiet", "-m", "move head")
            with self.assertRaises(RepositoryContextError):
                build_repository_context_package(repo, repository_map, ("pkg/core.py",), ("pkg/core.py",))

        with tempfile.TemporaryDirectory() as temp:
            repo, sha = init_repo(Path(temp), graph_files())
            repository_map = build_repository_map(repo, sha)
            tampered = replace(repository_map, map_sha256="0" * 64)
            with self.assertRaises(RepositoryContextError):
                build_repository_context_package(repo, tampered, ("pkg/core.py",), ("pkg/core.py",))
            entries = list(repository_map.entries)
            entries[0] = replace(entries[0], blob_sha="0" * 40)
            tampered = replace(repository_map, entries=tuple(entries))
            with self.assertRaises(RepositoryContextError):
                build_repository_context_package(repo, tampered, ("pkg/core.py",), ("pkg/core.py",))

    def test_seed_and_trusted_scope_validation_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, sha = init_repo(Path(temp), graph_files())
            repository_map = build_repository_map(repo, sha)
            cases = (
                (("missing.py",), ("pkg/core.py",)),
                (("pkg/sub.py", "pkg/core.py"), ("pkg/core.py",)),
                (("pkg/core.py", "pkg/core.py"), ("pkg/core.py",)),
                (("pkg/core.py",), ("z.py", "a.py")),
                (("pkg/core.py",), ("src/*",)),
                (("pkg/core.py",), ("src/**", "tests/**")),
                (("pkg/core.py",), ("../escape.py",)),
            )
            for seeds, scope in cases:
                with self.subTest(seeds=seeds, scope=scope), self.assertRaises(RepositoryContextError):
                    build_repository_context_package(repo, repository_map, seeds, scope)

    def test_non_utf8_nul_sensitive_and_size_bounds_fail_closed(self):
        cases = (
            ("binary.txt", b"\xff\xfe\xfd"),
            ("nul.txt", b"hello\x00world\n"),
            ("sensitive.txt", b"api_key = ZZZZZZZZZZZZZZZZZZZZZZZZ\n"),
        )
        for relative, content in cases:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temp:
                repo, sha = init_repo(Path(temp), {relative: content})
                repository_map = build_repository_map(repo, sha)
                with self.assertRaises(RepositoryContextError):
                    build_repository_context_package(repo, repository_map, (relative,), (relative,))

        with tempfile.TemporaryDirectory() as temp:
            repo, sha = init_repo(Path(temp), {"large.txt": "abcd\n"})
            repository_map = build_repository_map(repo, sha)
            with patch("scripts.agent_repository_context.MAX_CONTEXT_FILE_BYTES", 1):
                with self.assertRaises(RepositoryContextError):
                    build_repository_context_package(repo, repository_map, ("large.txt",), ("large.txt",))
            with patch("scripts.agent_repository_context.MAX_CONTEXT_TOTAL_BYTES", 1):
                with self.assertRaises(RepositoryContextError):
                    build_repository_context_package(repo, repository_map, ("large.txt",), ("large.txt",))
            with patch("scripts.agent_repository_context.MAX_CONTEXT_PACKAGE_BYTES", 32):
                with self.assertRaises(RepositoryContextError):
                    build_repository_context_package(repo, repository_map, ("large.txt",), ("large.txt",))

    def test_impact_expansion_and_context_file_count_limits_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, sha = init_repo(Path(temp), graph_files())
            repository_map = build_repository_map(repo, sha)
            with self.assertRaises(RepositoryContextError):
                build_repository_context_package(repo, repository_map, ("pkg/core.py",), ("pkg/core.py",), max_depth=3, max_paths=2)
            with patch("scripts.agent_repository_context.MAX_CONTEXT_FILES", 1):
                with self.assertRaises(RepositoryContextError):
                    build_repository_context_package(repo, repository_map, ("pkg/core.py",), ("pkg/core.py",), max_depth=3)

    def test_package_is_canonical_deterministic_and_seed_identity_changes_do_not_change_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, sha = init_repo(Path(temp), graph_files())
            first = self.build(repo, sha, seeds=("pkg/core.py",), scope=("pkg/core.py",), depth=3)
            second = self.build(repo, sha, seeds=("pkg/core.py",), scope=("pkg/core.py",), depth=3)
            first_raw = serialize_repository_context_package(first)
            self.assertEqual(first, second)
            self.assertEqual(first_raw, serialize_repository_context_package(second))
            self.assertEqual(first_raw, json.dumps(json.loads(first_raw), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))

            other = self.build(repo, sha, seeds=("app.py",), scope=("pkg/core.py",), depth=2)
            self.assertNotEqual(first.package_sha256, other.package_sha256)
            self.assertEqual(first.trusted_allowed_paths, other.trusted_allowed_paths)

    def test_serialized_schema_contains_no_scope_expansion_grader_or_reasoning_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, sha = init_repo(Path(temp), graph_files())
            package = self.build(repo, sha)
            data = json.loads(serialize_repository_context_package(package))

            def keys(value):
                result = set()
                if isinstance(value, dict):
                    result.update(value)
                    for item in value.values():
                        result.update(keys(item))
                elif isinstance(value, list):
                    for item in value:
                        result.update(keys(item))
                return result

            prohibited = {
                "expanded_allowed_paths", "writable_context", "grader", "grader_root",
                "grader_sha256", "known_solution", "expected_completion_class",
                "expected_human_action_reason", "hidden_reasoning", "transcript", "credentials",
            }
            self.assertFalse(keys(data) & prohibited)
            self.assertIn("trusted_allowed_paths", data)


if __name__ == "__main__":
    unittest.main()

"""Regression tests for the exact-SHA tracked-file repository map experiment."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.agent_repository_map import (
    RepositoryMapError,
    build_repository_map,
    discover_repository_impact,
    serialize_repository_map,
)


def git(repo: Path, *args: str, check: bool = True) -> str:
    executable = shutil.which("git")
    if executable is None:
        raise unittest.SkipTest("git is unavailable")
    environment = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "Repository Map Test",
        "GIT_AUTHOR_EMAIL": "repository-map@invalid.local",
        "GIT_COMMITTER_NAME": "Repository Map Test",
        "GIT_COMMITTER_EMAIL": "repository-map@invalid.local",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
        "LC_ALL": "C",
        "LANG": "C",
    }
    completed = subprocess.run(
        [executable, "-C", str(repo), *args],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if check and completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return completed.stdout.strip()


def write(repo: Path, relative: str, content: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def init_repo(root: Path, files: dict[str, str]) -> tuple[Path, str]:
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet", "--object-format=sha1")
    for relative, content in files.items():
        write(repo, relative, content)
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", "fixture")
    return repo, git(repo, "rev-parse", "HEAD")


def graph_files() -> dict[str, str]:
    return {
        "pkg/__init__.py": "\n",
        "pkg/core.py": "def normalize(value):\n    return value.strip()\n",
        "pkg/helper.py": "def helper():\n    return 'helper'\n",
        "pkg/sub.py": (
            "from . import helper\n"
            "from .core import normalize\n\n"
            "def value(text):\n"
            "    return helper.helper() + normalize(text)\n"
        ),
        "app.py": (
            "import os\n"
            "import pkg.sub\n"
            "from pkg import helper\n\n"
            "def run(value):\n"
            "    return pkg.sub.value(value) + helper.helper() + os.sep\n"
        ),
        "tests/test_app.py": (
            "from app import run\n\n"
            "def test_run():\n"
            "    assert run(' x ')\n"
        ),
        "README.md": "fixture\n",
    }


class RepositoryMapTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("git") is None:
            raise unittest.SkipTest("git is unavailable")

    def test_map_reads_exact_commit_objects_not_dirty_or_untracked_worktree_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, sha = init_repo(Path(temp), graph_files())
            first = build_repository_map(repo, sha)
            write(repo, "app.py", "this is not valid python anymore\n")
            write(repo, "untracked.py", "raise RuntimeError('must not be read')\n")
            second = build_repository_map(repo, sha)
            self.assertEqual(first, second)
            self.assertEqual(first.map_sha256, second.map_sha256)
            self.assertNotIn("untracked.py", {entry.path for entry in second.entries})
            app = next(entry for entry in second.entries if entry.path == "app.py")
            self.assertIn("pkg.sub", app.imported_modules)

    def test_expected_sha_mismatch_and_invalid_sha_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, sha = init_repo(Path(temp), {"a.py": "value = 1\n"})
            with self.assertRaises(RepositoryMapError):
                build_repository_map(repo, "0" * 40)
            with self.assertRaises(RepositoryMapError):
                build_repository_map(repo, sha.upper())

    def test_identical_commit_copy_has_identical_map_identity_and_canonical_serialization(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, sha = init_repo(root, graph_files())
            copy = root / "copy"
            shutil.copytree(repo, copy)
            first = build_repository_map(repo, sha)
            second = build_repository_map(copy, sha)
            self.assertEqual(first, second)
            first_raw = serialize_repository_map(first)
            second_raw = serialize_repository_map(second)
            self.assertEqual(first_raw, second_raw)
            self.assertEqual(
                first_raw,
                json.dumps(json.loads(first_raw), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
            )
            payload = json.loads(first_raw)
            self.assertEqual(payload["map_sha256"], first.map_sha256)
            without_digest = dict(payload)
            without_digest.pop("map_sha256")
            canonical_payload = json.dumps(without_digest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            self.assertEqual(hashlib.sha256(canonical_payload).hexdigest(), first.map_sha256)

    def test_absolute_relative_external_imports_and_reverse_edges_are_exact(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, sha = init_repo(Path(temp), graph_files())
            repository_map = build_repository_map(repo, sha)
            entries = {entry.path: entry for entry in repository_map.entries}

            self.assertEqual(entries["pkg/core.py"].module, "pkg.core")
            self.assertEqual(entries["pkg/__init__.py"].module, "pkg")
            self.assertIn("os", entries["app.py"].imported_modules)
            self.assertNotIn("os", {entry.module for entry in repository_map.entries if entry.module})
            self.assertEqual(
                entries["app.py"].local_dependencies,
                ("pkg/__init__.py", "pkg/helper.py", "pkg/sub.py"),
            )
            self.assertEqual(
                entries["pkg/sub.py"].local_dependencies,
                ("pkg/__init__.py", "pkg/core.py", "pkg/helper.py"),
            )
            self.assertEqual(entries["tests/test_app.py"].local_dependencies, ("app.py",))
            self.assertIn("pkg/sub.py", entries["pkg/core.py"].local_dependents)
            self.assertIn("app.py", entries["pkg/sub.py"].local_dependents)
            self.assertIn("tests/test_app.py", entries["app.py"].local_dependents)

    def test_impact_query_returns_bounded_forward_reverse_and_test_context(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, sha = init_repo(Path(temp), graph_files())
            repository_map = build_repository_map(repo, sha)

            forward = discover_repository_impact(repository_map, ("app.py",), max_depth=2)
            self.assertEqual(forward.seed_paths, ("app.py",))
            self.assertIn("pkg/sub.py", forward.context_paths)
            self.assertIn("pkg/core.py", forward.context_paths)
            self.assertEqual(forward.dependent_paths, ("tests/test_app.py",))
            self.assertEqual(forward.test_paths, ("tests/test_app.py",))

            reverse = discover_repository_impact(repository_map, ("pkg/core.py",), max_depth=3)
            self.assertEqual(
                reverse.dependent_paths,
                ("app.py", "pkg/sub.py", "tests/test_app.py"),
            )
            self.assertEqual(reverse.test_paths, ("tests/test_app.py",))
            self.assertFalse(hasattr(reverse, "allowed_paths"))

    def test_unknown_duplicate_unsorted_depth_and_expansion_limits_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, sha = init_repo(Path(temp), graph_files())
            repository_map = build_repository_map(repo, sha)
            cases = (
                (("missing.py",), 2, 256),
                (("pkg/core.py", "pkg/core.py"), 2, 256),
                (("pkg/sub.py", "pkg/core.py"), 2, 256),
                (("pkg/core.py",), -1, 256),
                (("pkg/core.py",), 99, 256),
                (("pkg/core.py",), 3, 2),
            )
            for seeds, depth, limit in cases:
                with self.subTest(seeds=seeds, depth=depth, limit=limit), self.assertRaises(RepositoryMapError):
                    discover_repository_impact(repository_map, seeds, max_depth=depth, max_paths=limit)

    def test_malformed_python_and_resource_bounds_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, sha = init_repo(Path(temp), {"broken.py": "def broken(:\n    pass\n"})
            with self.assertRaises(RepositoryMapError):
                build_repository_map(repo, sha)

        with tempfile.TemporaryDirectory() as temp:
            repo, sha = init_repo(Path(temp), {"a.py": "value = 1\n", "b.txt": "x\n", "c.txt": "y\n"})
            with patch("scripts.agent_repository_map.MAX_PYTHON_BLOB_BYTES", 1):
                with self.assertRaises(RepositoryMapError):
                    build_repository_map(repo, sha)
            with patch("scripts.agent_repository_map.MAX_TRACKED_FILES", 2):
                with self.assertRaises(RepositoryMapError):
                    build_repository_map(repo, sha)
            with patch("scripts.agent_repository_map.MAX_TOTAL_TRACKED_BYTES", 1):
                with self.assertRaises(RepositoryMapError):
                    build_repository_map(repo, sha)

    @unittest.skipIf(os.name == "nt", "case-sensitive path and symlink setup is not portable on Windows")
    def test_case_ambiguous_and_symlink_tracked_entries_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, sha = init_repo(Path(temp), {"Case.py": "x = 1\n", "case.py": "x = 2\n"})
            with self.assertRaises(RepositoryMapError):
                build_repository_map(repo, sha)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            git(repo, "init", "--quiet", "--object-format=sha1")
            write(repo, "target.py", "x = 1\n")
            (repo / "link.py").symlink_to("target.py")
            git(repo, "add", "-A")
            git(repo, "commit", "--quiet", "-m", "fixture")
            sha = git(repo, "rev-parse", "HEAD")
            with self.assertRaises(RepositoryMapError):
                build_repository_map(repo, sha)

    def test_relative_import_above_package_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, sha = init_repo(
                Path(temp),
                {
                    "pkg/__init__.py": "\n",
                    "pkg/mod.py": "from ..outside import value\n",
                },
            )
            with self.assertRaises(RepositoryMapError):
                build_repository_map(repo, sha)


if __name__ == "__main__":
    unittest.main()

import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from bootstrap.generator import ALLOWLIST, GENERATED_TARGET_MARKER, render

ROOT = Path(__file__).resolve().parents[1]


def run_validator(target: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/validate_repository.py"],
        cwd=target,
        capture_output=True,
        text=True,
        check=False,
    )


def run_generated_unit_tests_path(target: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-euo",
            "pipefail",
            "-c",
            "if [ -d tests ]; then "
            "python -m unittest discover -s tests; "
            "else "
            "python scripts/public_export_guard.py . && "
            "python scripts/validate_repository.py; "
            "fi",
        ],
        cwd=target,
        capture_output=True,
        text=True,
        check=False,
    )


def queue_python_step(step_name: str) -> str:
    queue = (ROOT / ".github/workflows/claude-queue.yml").read_text(encoding="utf-8")
    step = queue.split(f"- name: {step_name}", 1)[1]
    source = step.split("python3 - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
    return textwrap.dedent(source)


def run_queue_materializer(
    repository: Path,
    scratch: Path,
    *,
    files: list[dict[str, object]],
) -> subprocess.CompletedProcess[bytes]:
    base_sha = "a" * 40
    runner_temp = scratch / "runner"
    artifact = runner_temp / "queue-candidate"
    artifact.mkdir(parents=True)
    candidate = {
        "version": 1,
        "base_sha": base_sha,
        "branch_name": "claude-issue-135-symlink-test",
        "files": files,
    }
    raw = json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode()
    (artifact / "candidate.json").write_bytes(raw)
    environment = os.environ.copy()
    environment.update(
        {
            "RUNNER_TEMP": str(runner_temp),
            "EXPECTED_DIGEST": hashlib.sha256(raw).hexdigest(),
            "BASE_SHA": base_sha,
        }
    )
    return subprocess.run(
        [sys.executable, "-c", queue_python_step("Verify digest and materialize complete candidate bytes")],
        cwd=repository,
        env=environment,
        capture_output=True,
        check=False,
    )


def run_queue_packager(
    repository: Path,
    scratch: Path,
    *,
    base_sha: str,
) -> subprocess.CompletedProcess[bytes]:
    runner_temp = scratch / "runner"
    runner_temp.mkdir()
    output = scratch / "github-output"
    environment = os.environ.copy()
    environment.update(
        {
            "BASE_SHA": base_sha,
            "ISSUE_NUMBER": "135",
            "CLAUDE_BRANCH": "claude-issue-135-symlink-test",
            "RUNNER_TEMP": str(runner_temp),
            "GITHUB_OUTPUT": str(output),
        }
    )
    return subprocess.run(
        [sys.executable, "-c", queue_python_step("Package complete candidate bytes and manifest")],
        cwd=repository,
        env=environment,
        capture_output=True,
        check=False,
        timeout=5,
    )


class BootstrapTest(unittest.TestCase):
    def test_rendered_allowlist_and_target_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render(target, "example-owner")
            for path in ALLOWLIST:
                self.assertTrue((target / path).is_file(), path)
            self.assertTrue((target / "README.md").read_text(encoding="utf-8").strip())
            self.assertTrue((target / "LICENSE").read_text(encoding="utf-8").strip())
            checklist = (target / "INSTALL_CHECKLIST.md").read_text(encoding="utf-8")
            self.assertIn(GENERATED_TARGET_MARKER, checklist)
            self.assertIn("example-owner", checklist)
            self.assertNotIn("notion", checklist.lower())
            self.assertIn("Queue failure creates no routine Issue or Pull Request comment", checklist)
            self.assertIn("Queue recovery is bounded, deterministic, idempotent, non-notifying", checklist)
            self.assertIn("owner-authored existing-PR-base block", checklist)
            self.assertIn("digest-bound full-byte handoff", checklist)
            self.assertIn("ordinary Issues still target the default branch", checklist)
            self.assertIn("one unchanged default-branch SHA", checklist)
            self.assertIn("explicit label evidence", checklist)
            self.assertIn("single-use", checklist)
            self.assertIn("automation-internal-stops", checklist)
            self.assertIn("automation-stops/pr-<number>/<sha>/<REASON>.json", checklist)
            self.assertIn("never posted as Issue or Pull Request comments", checklist)
            self.assertIn("failed audit or moved head", checklist)
            self.assertIn("immutable trusted request timestamp", checklist)
            self.assertIn("latest immutable clean evidence", checklist)
            self.assertIn("three canonical account/provider UI reason codes", checklist)
            self.assertIn("github-actions[bot]", checklist)
            self.assertIn("automatic-resumption condition", checklist)
            self.assertFalse((target / "bootstrap").exists())
            self.assertFalse((target / "tests").exists())

            for workflow_path in (
                ".github/workflows/unit-tests.yml",
                ".github/workflows/claude-queue.yml",
                ".github/workflows/trusted-checks.yml",
            ):
                with self.subTest(workflow_path=workflow_path):
                    workflow = (target / workflow_path).read_text(encoding="utf-8")
                    self.assertIn("if [ -d tests ]; then", workflow)
                    self.assertIn("python -m unittest discover -s tests", workflow)
                    self.assertIn("python scripts/public_export_guard.py .", workflow)
                    self.assertIn("python scripts/validate_repository.py", workflow)

            result = run_generated_unit_tests_path(target)
            self.assertEqual(
                result.returncode,
                0,
                msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
            )
            self.assertIn("public export guard: clean", result.stdout)
            self.assertIn("repository validation: clean", result.stdout)

    def test_no_tests_repository_without_generated_identity_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render(target, "example-owner")
            (target / "INSTALL_CHECKLIST.md").unlink()
            self.assertFalse((target / "tests").exists())

            result = run_generated_unit_tests_path(target)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("repository identity is ambiguous", result.stderr)

    def test_fresh_source_identity_requires_generator_without_bootstrap_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render(target, "example-owner")
            (target / "INSTALL_CHECKLIST.md").unlink()
            source_marker = target / "tests/test_bootstrap.py"
            source_marker.parent.mkdir(parents=True)
            source_marker.write_text("# durable source marker\n", encoding="utf-8")
            self.assertFalse((target / "bootstrap").exists())

            result = run_validator(target)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "Foundation source checkout is missing bootstrap/generator.py",
                result.stderr,
            )

    def test_generated_marker_remains_authoritative_when_target_adds_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render(target, "example-owner")
            source_marker = target / "tests/test_bootstrap.py"
            source_marker.parent.mkdir(parents=True)
            source_marker.write_text("# target-specific tests are allowed\n", encoding="utf-8")

            result = run_validator(target)
            self.assertEqual(
                result.returncode,
                0,
                msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
            )

    def test_public_identity_files_are_distributed(self):
        for path in (
            "README.md",
            "LICENSE",
            "SECURITY.md",
            "scripts/supervisor_final_guard.py",
            "scripts/supervisor_queue_recovery.py",
            "scripts/supervisor_queue_recovery_v2.py",
            "scripts/supervisor_queue_recovery_v3.py",
        ):
            with self.subTest(path=path):
                self.assertIn(path, ALLOWLIST)

    def test_generated_queue_workflow_has_byte_parity(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render(target, "example-owner")
            source = ROOT / ".github/workflows/claude-queue.yml"
            self.assertEqual(
                (target / ".github/workflows/claude-queue.yml").read_bytes(),
                source.read_bytes(),
            )

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support is required")
    def test_queue_materializer_replaces_leaf_symlink_without_following(self):
        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory)
            repository = scratch / "repo"
            repository.mkdir()
            outside = scratch / "outside.txt"
            outside.write_bytes(b"outside remains unchanged\n")
            target = repository / "tracked-link.txt"
            target.symlink_to(outside)
            replacement = b"verified candidate bytes\n"
            result = run_queue_materializer(
                repository,
                scratch,
                files=[
                    {
                        "path": "tracked-link.txt",
                        "mode": "100644",
                        "deleted": False,
                        "sha256": hashlib.sha256(replacement).hexdigest(),
                        "content_base64": base64.b64encode(replacement).decode(),
                    }
                ],
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            self.assertEqual(outside.read_bytes(), b"outside remains unchanged\n")
            self.assertFalse(target.is_symlink())
            self.assertEqual(target.read_bytes(), replacement)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support is required")
    def test_queue_materializer_deletes_dangling_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory)
            repository = scratch / "repo"
            repository.mkdir()
            target = repository / "dangling-link.txt"
            target.symlink_to(scratch / "missing-target")
            result = run_queue_materializer(
                repository,
                scratch,
                files=[
                    {
                        "path": "dangling-link.txt",
                        "mode": None,
                        "deleted": True,
                        "sha256": hashlib.sha256(b"").hexdigest(),
                        "content_base64": "",
                    }
                ],
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            self.assertFalse(os.path.lexists(target))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support is required")
    def test_queue_materializer_rejects_symlink_parent_without_writing_outside(self):
        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory)
            repository = scratch / "repo"
            repository.mkdir()
            outside = scratch / "outside"
            outside.mkdir()
            (repository / "subdir").symlink_to(outside, target_is_directory=True)
            payload = b"must not escape\n"
            result = run_queue_materializer(
                repository,
                scratch,
                files=[
                    {
                        "path": "subdir/escaped.txt",
                        "mode": "100644",
                        "deleted": False,
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "content_base64": base64.b64encode(payload).decode(),
                    }
                ],
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((outside / "escaped.txt").exists())

    @unittest.skipUnless(
        hasattr(os, "symlink") and hasattr(os, "mkfifo"),
        "symlink and FIFO support are required",
    )
    def test_queue_packager_rejects_tracked_symlink_before_reading_target(self):
        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory)
            repository = scratch / "repo"
            repository.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repository, check=True)
            subprocess.run(["git", "config", "user.name", "Queue Test"], cwd=repository, check=True)
            original_target = scratch / "original.txt"
            original_target.write_bytes(b"base target\n")
            tracked = repository / "tracked-link"
            tracked.symlink_to(original_target)
            subprocess.run(["git", "add", "tracked-link"], cwd=repository, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=repository, check=True)
            base_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()

            fifo = scratch / "external-fifo"
            os.mkfifo(fifo)
            tracked.unlink()
            tracked.symlink_to(fifo)
            result = run_queue_packager(repository, scratch, base_sha=base_sha)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"candidate files must be regular files", result.stderr)
            self.assertFalse((scratch / "runner/queue-candidate/candidate.json").exists())

    @unittest.skipUnless(
        hasattr(os, "symlink") and hasattr(os, "mkfifo"),
        "symlink and FIFO support are required",
    )
    def test_queue_packager_rejects_untracked_symlink_before_reading_target(self):
        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory)
            repository = scratch / "repo"
            repository.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repository, check=True)
            subprocess.run(["git", "config", "user.name", "Queue Test"], cwd=repository, check=True)
            (repository / "base.txt").write_bytes(b"base\n")
            subprocess.run(["git", "add", "base.txt"], cwd=repository, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=repository, check=True)
            base_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()

            fifo = scratch / "external-fifo"
            os.mkfifo(fifo)
            (repository / "untracked-link").symlink_to(fifo)
            result = run_queue_packager(repository, scratch, base_sha=base_sha)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"candidate files must be regular files", result.stderr)
            self.assertFalse((scratch / "runner/queue-candidate/candidate.json").exists())


if __name__ == "__main__":
    unittest.main()

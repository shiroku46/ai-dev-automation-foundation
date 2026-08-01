import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from bootstrap.generator import ALLOWLIST, GENERATED_TARGET_MARKER, render


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

            unit_tests = (target / ".github/workflows/unit-tests.yml").read_text(encoding="utf-8")
            self.assertIn("if [ -d tests ]; then", unit_tests)
            self.assertIn("python -m unittest discover -s tests", unit_tests)
            self.assertIn("python scripts/public_export_guard.py .", unit_tests)
            self.assertIn("python scripts/validate_repository.py", unit_tests)

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


if __name__ == "__main__":
    unittest.main()

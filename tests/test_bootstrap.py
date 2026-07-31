import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from bootstrap.generator import (
    ALLOWLIST,
    GENERATED_TARGET_MARKER,
    GENERATED_TARGET_MARKER_CONTENT,
    render,
)


def run_validator(target: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/validate_repository.py"],
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
            self.assertEqual(
                (target / GENERATED_TARGET_MARKER).read_text(encoding="utf-8"),
                GENERATED_TARGET_MARKER_CONTENT,
            )
            checklist = (target / "INSTALL_CHECKLIST.md").read_text(encoding="utf-8")
            self.assertIn("example-owner", checklist)
            self.assertNotIn("notion", checklist.lower())
            self.assertFalse((target / "bootstrap").exists())

            result = run_validator(target)
            self.assertEqual(
                result.returncode,
                0,
                msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
            )
            self.assertIn("repository validation: clean", result.stdout)

    def test_source_mode_requires_generator_even_when_directory_is_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render(target, "example-owner")
            (target / GENERATED_TARGET_MARKER).unlink()

            result = run_validator(target)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "Foundation source checkout is missing bootstrap/generator.py",
                result.stderr,
            )

    def test_generated_marker_does_not_hide_bootstrap_source(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render(target, "example-owner")
            (target / "bootstrap").mkdir()

            result = run_validator(target)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "generated target must not contain the Foundation Bootstrap source directory",
                result.stderr,
            )

    def test_public_identity_files_are_distributed(self):
        self.assertIn("README.md", ALLOWLIST)
        self.assertIn("LICENSE", ALLOWLIST)
        self.assertIn("SECURITY.md", ALLOWLIST)


if __name__ == "__main__":
    unittest.main()

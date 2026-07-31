import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from bootstrap.generator import ALLOWLIST, render


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

    def test_source_bootstrap_directory_requires_generator(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render(target, "example-owner")
            (target / "bootstrap").mkdir()

            result = run_validator(target)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "Foundation source checkout is missing bootstrap/generator.py",
                result.stderr,
            )

    def test_public_identity_files_are_distributed(self):
        self.assertIn("README.md", ALLOWLIST)
        self.assertIn("LICENSE", ALLOWLIST)
        self.assertIn("SECURITY.md", ALLOWLIST)


if __name__ == "__main__":
    unittest.main()

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


class BootstrapTest(unittest.TestCase):
    def test_rendered_allowlist_and_target_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render(target, "example-owner")
            for path in ALLOWLIST:
                self.assertTrue((target / path).is_file(), path)
            self.assertTrue((target / "README.md").read_text(encoding="utf-8").strip())
            self.assertTrue((target / "LICENSE").read_text(encoding="utf-8").strip())
            self.assertTrue((target / "docs/MINIMUM_SAFETY_PROFILE.md").is_file())
            checklist = (target / "INSTALL_CHECKLIST.md").read_text(encoding="utf-8")
            self.assertIn(GENERATED_TARGET_MARKER, checklist)
            self.assertIn("example-owner", checklist)

            # Phase 0 must be the first operational section.
            self.assertLess(checklist.index("## Phase 0"), checklist.index("## Minimum safety profile"))
            self.assertIn("authorize this exact repository", checklist)
            self.assertIn("Create a Codex Environment", checklist)
            self.assertIn("claude setup-token", checklist)
            self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", checklist)
            self.assertIn("never paste it", checklist)

            # Practical minimum-safety and tiered-review contract.
            self.assertIn("foundation-task-scope", checklist)
            self.assertIn("GitHub-visible remote head SHA", checklist)
            self.assertIn("Low-risk", checklist)
            self.assertIn("Standard-risk", checklist)
            self.assertIn("Protected changes require clean exact-SHA Codex", checklist)
            self.assertIn("neutral review-required state", checklist)
            self.assertIn("does not actively mention `@codex`", checklist)
            self.assertIn("Provider setup/error replies never count", checklist)
            self.assertIn("one final live", checklist)
            self.assertIn("exact expected head SHA", checklist)
            self.assertFalse((target / "bootstrap").exists())

            result = run_validator(target)
            self.assertEqual(
                result.returncode,
                0,
                msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
            )
            self.assertIn("repository validation: clean", result.stdout)

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
            "docs/MINIMUM_SAFETY_PROFILE.md",
            "scripts/supervisor_final_guard.py",
            "scripts/supervisor_queue_recovery.py",
            "scripts/supervisor_queue_recovery_v2.py",
            "scripts/supervisor_queue_recovery_v3.py",
        ):
            with self.subTest(path=path):
                self.assertIn(path, ALLOWLIST)


if __name__ == "__main__":
    unittest.main()

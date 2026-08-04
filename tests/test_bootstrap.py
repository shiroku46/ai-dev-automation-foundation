"""Bootstrap byte-parity and generated-target safety tests."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from bootstrap.generator import ALLOWLIST, GENERATED_TARGET_MARKER, MANAGED_FILES, render

ROOT = Path(__file__).resolve().parents[1]


def validate(target: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/validate_repository.py"],
        cwd=target,
        capture_output=True,
        text=True,
        check=False,
    )


class BootstrapTest(unittest.TestCase):
    def test_allowlist_alias_and_no_recursive_bootstrap(self):
        self.assertEqual(ALLOWLIST, MANAGED_FILES)
        self.assertNotIn("bootstrap/generator.py", MANAGED_FILES)
        self.assertFalse(any(path.startswith("tests/") for path in MANAGED_FILES))

    def test_render_copies_every_managed_file_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render(target, "owner")
            for relative in MANAGED_FILES:
                self.assertEqual(
                    (target / relative).read_bytes(),
                    (ROOT / relative).read_bytes(),
                    relative,
                )
            self.assertFalse((target / "bootstrap").exists())
            self.assertFalse((target / "tests").exists())

    def test_install_checklist_has_phase_zero_and_github_only_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render(target, "owner")
            checklist = (target / "INSTALL_CHECKLIST.md").read_text(encoding="utf-8")
            self.assertIn(GENERATED_TARGET_MARKER, checklist)
            self.assertIn("## Phase 0", checklist)
            self.assertIn("GitHub coordinator review", checklist)
            self.assertIn("Codex and Claude setup is optional", checklist)
            self.assertIn("automation-internal-stops", checklist)
            self.assertIn("human_action_required: false", checklist)

    def test_install_checklist_has_bounded_queue_recovery_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render(target, "owner")
            checklist = (target / "INSTALL_CHECKLIST.md").read_text(encoding="utf-8")
            self.assertIn("## Bounded Queue recovery migration", checklist)
            self.assertIn("rerun the Bootstrap renderer", checklist)
            self.assertIn("copy every managed file byte-for-byte", checklist)
            self.assertIn("one classifier-approved bounded retry", checklist)
            self.assertIn("Do not copy only the reconciliation workflow", checklist)

    def test_generated_target_validator_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render(target, "owner")
            result = validate(target)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_generated_target_workflows_are_exact_source_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render(target, "owner")
            for relative in (
                ".github/workflows/ci.yml",
                ".github/workflows/unit-tests.yml",
                ".github/workflows/claude-queue.yml",
                ".github/workflows/ci-reconcile.yml",
                ".github/workflows/supervisor.yml",
            ):
                self.assertEqual((target / relative).read_bytes(), (ROOT / relative).read_bytes())

    def test_generated_reconciliation_has_checkpoint_and_retry_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render(target, "owner")
            workflow = (target / ".github/workflows/ci-reconcile.yml").read_text(encoding="utf-8")
            for marker in (
                'workflows: ["CI", "Unit Tests", "Claude Issue Queue"]',
                "schedule:",
                "queue_recovery:",
                "queue-complete-",
                "queue-wip-",
                '["git", "apply", "--check"',
                '["git", "commit-tree"',
                "should_auto_retry",
                "candidate_execution_with_write_token: `false`",
                "human_action_required: `false`",
            ):
                self.assertIn(marker, workflow)

    def test_tampering_or_missing_managed_file_fails_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render(target, "owner")
            (target / ".github/workflows/supervisor.yml").write_text(
                "name: broken\non:\njobs:\n", encoding="utf-8"
            )
            result = validate(target)
            self.assertNotEqual(result.returncode, 0)

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render(target, "owner")
            (target / "scripts/github_coordinator_supervisor.py").unlink()
            result = validate(target)
            self.assertNotEqual(result.returncode, 0)

    def test_renderer_refuses_to_overwrite_source_tree(self):
        with self.assertRaises(ValueError):
            render(ROOT, "owner")


if __name__ == "__main__":
    unittest.main()

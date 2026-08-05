"""Regression coverage for Foundation source versus generated product tests."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from bootstrap.generator import render

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA = "a" * 40
INSTALLED_AT = "2026-08-05T00:00:00Z"


class GeneratedUnitWorkflowTest(unittest.TestCase):
    def test_workflow_uses_source_only_bootstrap_identity(self):
        workflow = (ROOT / ".github/workflows/unit-tests.yml").read_text(encoding="utf-8")
        self.assertIn("if [ -f bootstrap/generator.py ]; then", workflow)
        self.assertNotIn("if [ -d tests ]; then", workflow)
        self.assertIn("python -m unittest discover -s tests", workflow)
        self.assertIn("python scripts/public_export_guard.py .", workflow)
        self.assertIn("python scripts/validate_repository.py", workflow)

    def test_generated_target_does_not_execute_product_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render(
                target,
                "owner",
                mode="new-repository",
                source_sha=SOURCE_SHA,
                installed_at=INSTALLED_AT,
            )
            product_tests = target / "tests"
            product_tests.mkdir()
            (product_tests / "test_product.py").write_text(
                "raise RuntimeError('product tests must not run in Foundation-native validation')\n",
                encoding="utf-8",
            )
            self.assertFalse((target / "bootstrap/generator.py").exists())
            result = subprocess.run(
                [
                    "bash",
                    "-eu",
                    "-c",
                    "if [ -f bootstrap/generator.py ]; then "
                    "python -m unittest discover -s tests; "
                    "else python scripts/public_export_guard.py . && "
                    "python scripts/validate_repository.py; fi",
                ],
                cwd=target,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

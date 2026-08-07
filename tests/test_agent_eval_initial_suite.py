"""Checked-in initial coding-agent evaluation suite tests."""
from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from scripts import agent_eval_suite_contract as suite
from scripts.agent_eval_grader_contract import GRADER_IDENTITY_ENVIRONMENT_KEYS

ROOT = Path(__file__).resolve().parents[1]
SUITE_ROOT = ROOT / "evaluation/initial"
CATALOG = SUITE_ROOT / "catalog.json"
EXPECTED_FOUNDATION_SHA = "1300059356cedb8379ef16867b56b4ca9fe81d26"
EXPECTED_CATALOG_SHA = "acc240dcdbaca5bc5be41104e6a6f2a57a0879136d82ae08f30a998521693cf8"
EXPECTED_MANIFEST_SHAS = (
    "2fa6a9bba273fd7df081ff73602f89c1f5b841c6f69263e557874f88de412f05",
    "3f691fcc9c264e0769bd96f5f956171d244110fe91e9c800adad7848ad38af29",
    "162b784a5f54e3ca9b69396b663758a82f63434ad7ec562f7bbca291839b5cd4",
)


class InitialEvaluationSuiteTest(unittest.TestCase):
    def test_checked_in_initial_slice_loads_and_binds_three_tasks(self):
        raw = CATALOG.read_bytes()
        loaded = suite.load_evaluation_suite(raw, SUITE_ROOT)
        self.assertEqual(loaded.catalog.suite_id, "foundation.initial")
        self.assertEqual(loaded.catalog.suite_version, 1)
        self.assertEqual(loaded.catalog.foundation_sha, EXPECTED_FOUNDATION_SHA)
        self.assertEqual(loaded.catalog.task_count, 3)
        self.assertEqual(loaded.catalog.catalog_sha256, EXPECTED_CATALOG_SHA)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), EXPECTED_CATALOG_SHA)
        self.assertEqual(
            tuple(task.entry.task_id for task in loaded.tasks),
            ("foundation.task-001", "foundation.task-002", "foundation.task-003"),
        )
        self.assertEqual(
            tuple(task.manifest.category for task in loaded.tasks),
            ("bug_fix", "test_addition", "multi_file_change"),
        )
        self.assertEqual(
            tuple(task.entry.manifest_sha256 for task in loaded.tasks),
            EXPECTED_MANIFEST_SHAS,
        )
        self.assertEqual(
            tuple(task.manifest.expected_completion_class for task in loaded.tasks),
            ("change_required", "change_required", "change_required"),
        )
        self.assertEqual(
            tuple(task.manifest.grader.runtime for task in loaded.tasks),
            ("python3.12", "python3.12", "python3.12"),
        )
        self.assertEqual(
            tuple(task.manifest.grader.network_mode for task in loaded.tasks),
            ("disabled", "disabled", "disabled"),
        )
        self.assertTrue(all(task.fixture_bundle.file_count == 2 for task in loaded.tasks))
        self.assertTrue(all(task.grader_bundle.file_count == 1 for task in loaded.tasks))

    def test_graders_use_runner_identity_without_hard_coded_bundle_digests(self):
        loaded = suite.load_evaluation_suite(CATALOG.read_bytes(), SUITE_ROOT)
        for task in loaded.tasks:
            grader_path = SUITE_ROOT / task.entry.grader_root / "grader/grade.py"
            source = grader_path.read_text(encoding="utf-8")
            with self.subTest(task=task.entry.task_id):
                self.assertNotIn(task.entry.manifest_sha256, source)
                self.assertNotIn(task.manifest.grader.sha256, source)
                for key in GRADER_IDENTITY_ENVIRONMENT_KEYS:
                    self.assertIn(key, source)


if __name__ == "__main__":
    unittest.main()

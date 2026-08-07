"""Checked-in initial coding-agent evaluation suite tests."""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import agent_eval_suite_contract as suite
from scripts.agent_eval_grader_contract import (
    GRADER_IDENTITY_ENVIRONMENT_KEYS,
    GraderResultExpectation,
    build_grader_identity_environment,
    parse_grader_result,
    validate_grader_process_result,
)

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

SOLUTIONS = {
    "foundation.task-001": {
        "src/ranges.py": """def clamp(value, lower, upper):
    \"\"\"Clamp value to the inclusive [lower, upper] interval.\"\"\"
    if lower > upper:
        raise ValueError(\"lower must not exceed upper\")
    return min(upper, max(value, lower))
""",
    },
    "foundation.task-002": {
        "tests/test_slug.py": """import unittest

from src.slug import slugify


class SlugifyTests(unittest.TestCase):
    def test_spaces_become_separators(self):
        self.assertEqual(slugify(\"Alpha Beta\"), \"alpha-beta\")

    def test_repeated_punctuation_collapses_to_single_separator(self):
        self.assertEqual(slugify(\"Alpha---Beta\"), \"alpha-beta\")


if __name__ == \"__main__\":
    unittest.main()
""",
    },
    "foundation.task-003": {
        "src/settings.py": """DEFAULT_RETRY_COUNT = 2


def retry_count(overrides):
    \"\"\"Return the configured retry count.\"\"\"
    return int(overrides.get(\"max_retries\", DEFAULT_RETRY_COUNT))
""",
        "tests/test_settings.py": """import unittest

from src.settings import retry_count


class RetryCountTests(unittest.TestCase):
    def test_retry_override(self):
        self.assertEqual(retry_count({\"max_retries\": 5}), 5)

    def test_legacy_retry_override_is_ignored(self):
        self.assertEqual(retry_count({\"retries\": 5}), 2)

    def test_retry_default(self):
        self.assertEqual(retry_count({}), 2)


if __name__ == \"__main__\":
    unittest.main()
""",
    },
}


class InitialEvaluationSuiteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loaded = suite.load_evaluation_suite(CATALOG.read_bytes(), SUITE_ROOT)

    def test_checked_in_initial_slice_loads_and_binds_three_tasks(self):
        raw = CATALOG.read_bytes()
        loaded = self.loaded
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
        for task in self.loaded.tasks:
            grader_path = SUITE_ROOT / task.entry.grader_root / "grader/grade.py"
            source = grader_path.read_text(encoding="utf-8")
            with self.subTest(task=task.entry.task_id):
                self.assertNotIn(task.entry.manifest_sha256, source)
                self.assertNotIn(task.manifest.grader.sha256, source)
                for key in GRADER_IDENTITY_ENVIRONMENT_KEYS:
                    self.assertIn(key, source)

    def _run_grader(self, task, workspace, result_path):
        expected = GraderResultExpectation(
            task_id=task.entry.task_id,
            task_version=task.entry.task_version,
            manifest_sha256=task.entry.manifest_sha256,
            grader_sha256=task.manifest.grader.sha256,
            foundation_sha=EXPECTED_FOUNDATION_SHA,
            base_sha="1" * 40,
            candidate_sha="2" * 40,
        )
        grader_path = SUITE_ROOT / task.entry.grader_root / task.manifest.grader.entrypoint
        completed = subprocess.run(
            [
                sys.executable,
                str(grader_path),
                "--workspace",
                str(workspace),
                "--result",
                str(result_path),
            ],
            env=dict(build_grader_identity_environment(expected)),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=task.manifest.grader.timeout_seconds,
            check=False,
        )
        parsed = parse_grader_result(result_path.read_bytes(), expected=expected)
        return validate_grader_process_result(completed.returncode, parsed)

    def test_graders_reject_baselines_and_accept_known_solutions(self):
        for task in self.loaded.tasks:
            with self.subTest(task=task.entry.task_id), tempfile.TemporaryDirectory() as temp:
                temp_root = Path(temp)
                workspace = temp_root / "workspace"
                shutil.copytree(SUITE_ROOT / task.entry.fixture_root, workspace)
                baseline = self._run_grader(task, workspace, temp_root / "baseline.json")
                self.assertEqual(baseline.outcome, "failed")

                for relative, content in SOLUTIONS[task.entry.task_id].items():
                    (workspace / relative).write_text(content, encoding="utf-8")
                solved = self._run_grader(task, workspace, temp_root / "solved.json")
                self.assertEqual(solved.outcome, "passed")


if __name__ == "__main__":
    unittest.main()

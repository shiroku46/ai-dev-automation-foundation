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
from scripts.agent_handoff_contract import parse_handoff_bundle

ROOT = Path(__file__).resolve().parents[1]
SUITE_ROOT = ROOT / "evaluation/initial"
CATALOG = SUITE_ROOT / "catalog.json"
EXPECTED_FOUNDATION_SHA = "ebebb4116c8e28993f2b80bee20e4a8a21f81a31"
EXPECTED_CATALOG_SHA = "7b6227b2e4afe02b8803247dbaa2c9c7653450c70126202401bf26a5328e08a0"
EXPECTED_MANIFEST_SHAS = ('2fa6a9bba273fd7df081ff73602f89c1f5b841c6f69263e557874f88de412f05', '3f691fcc9c264e0769bd96f5f956171d244110fe91e9c800adad7848ad38af29', '162b784a5f54e3ca9b69396b663758a82f63434ad7ec562f7bbca291839b5cd4', 'dd5b1014050574ec80eb0eb35ec96fb42e084663b1ca3d55cf003b3bf151ff47', '2003d57bccf62e3f7e1ba9aea61aa8a30d42d5e7105129a063313e022f2418ab', 'a6c9e017d8b247158ac2092628406be3c7bbf9f1b75b0b77013adf025efd2449', 'ed1b5282b51eda71ae308863a7f86e788af3d8dd08d627b0c32ce2655ace73f4', '82f775cbef0d1865beafc057fe15606e2283d207e5a4719826ccbd7381e9487d', '2b35418f72a970ab6e9b49ba2913f5d7ea065b362a9931026d6af59a797d9f67', '23e914ab54d536ece037419cc50dbf1d279606cd18347f891a6b45fd0fc760d2')
HANDOFF_REPOSITORY = "shiroku46/eval-handoff"
HANDOFF_ISSUE = 10
HANDOFF_BASE_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
HANDOFF_CANDIDATE_SHA = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

SOLUTIONS = {'foundation.task-001': {'src/ranges.py': 'def clamp(value, lower, upper):\n    """Clamp value to the inclusive [lower, upper] interval."""\n    if lower > upper:\n        raise ValueError("lower must not exceed upper")\n    return min(upper, max(value, lower))\n'}, 'foundation.task-002': {'tests/test_slug.py': 'import unittest\n\nfrom src.slug import slugify\n\n\nclass SlugifyTests(unittest.TestCase):\n    def test_spaces_become_separators(self):\n        self.assertEqual(slugify("Alpha Beta"), "alpha-beta")\n\n    def test_repeated_punctuation_collapses_to_single_separator(self):\n        self.assertEqual(slugify("Alpha---Beta"), "alpha-beta")\n\n\nif __name__ == "__main__":\n    unittest.main()\n'}, 'foundation.task-003': {'src/settings.py': 'DEFAULT_RETRY_COUNT = 2\n\n\ndef retry_count(overrides):\n    """Return the configured retry count."""\n    return int(overrides.get("max_retries", DEFAULT_RETRY_COUNT))\n', 'tests/test_settings.py': 'import unittest\n\nfrom src.settings import retry_count\n\n\nclass RetryCountTests(unittest.TestCase):\n    def test_retry_override(self):\n        self.assertEqual(retry_count({"max_retries": 5}), 5)\n\n    def test_legacy_retry_override_is_ignored(self):\n        self.assertEqual(retry_count({"retries": 5}), 2)\n\n    def test_retry_default(self):\n        self.assertEqual(retry_count({}), 2)\n\n\nif __name__ == "__main__":\n    unittest.main()\n'}, 'foundation.task-004': {'src/formatter.py': 'def headline(value):\n    """Return a display headline."""\n    return value.strip().title()\n'}, 'foundation.task-005': {'.github/workflows/check.yml': 'name: Synthetic Check\non: [push]\npermissions:\n  contents: read\njobs:\n  check:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo synthetic\n'}, 'foundation.task-007': {'src/route.py': 'def implementation_route(provider_available):\n    """Choose the implementation route without requiring human action for optional provider outages."""\n    if provider_available:\n        return "provider"\n    return "github-direct"\n'}, 'foundation.task-009': {'src/local_config.py': 'def local_feature_enabled():\n    """Return whether the repository-local feature is enabled."""\n    return True\n'}, 'foundation.task-010': {'src/resume.py': 'def checkpoint():\n    """Return the current resume checkpoint."""\n    return "ready"\n'}}
STALE_MUTATION = {"src/stable.py": 'def status():\n    """Return the current synthetic status."""\n    return "legacy"\n'}
HUMAN_MUTATION = {"docs/connection.md": 'Synthetic integration state: connected.\n'}


class InitialEvaluationSuiteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loaded = suite.load_evaluation_suite(CATALOG.read_bytes(), SUITE_ROOT)

    def test_checked_in_initial_slice_loads_and_binds_ten_tasks(self):
        raw = CATALOG.read_bytes()
        loaded = self.loaded
        self.assertEqual(loaded.catalog.suite_id, "foundation.initial")
        self.assertEqual(loaded.catalog.suite_version, 3)
        self.assertEqual(loaded.catalog.foundation_sha, EXPECTED_FOUNDATION_SHA)
        self.assertEqual(loaded.catalog.task_count, 10)
        self.assertEqual(loaded.catalog.catalog_sha256, EXPECTED_CATALOG_SHA)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), EXPECTED_CATALOG_SHA)
        self.assertEqual(
            tuple(task.entry.task_id for task in loaded.tasks),
            tuple(f"foundation.task-{index:03d}" for index in range(1, 11)),
        )
        self.assertEqual(
            tuple(task.manifest.category for task in loaded.tasks),
            ('bug_fix', 'test_addition', 'multi_file_change', 'scope_trap', 'protected_boundary', 'stale_evidence', 'provider_unavailable', 'human_only', 'human_only', 'handoff_resume'),
        )
        self.assertEqual(
            tuple(task.entry.manifest_sha256 for task in loaded.tasks),
            EXPECTED_MANIFEST_SHAS,
        )
        self.assertEqual(
            tuple(task.manifest.expected_completion_class for task in loaded.tasks),
            ('change_required', 'change_required', 'change_required', 'change_required', 'change_required', 'no_change_required', 'change_required', 'human_action_required', 'change_required', 'change_required'),
        )
        self.assertEqual(
            tuple(task.manifest.risk_tier for task in loaded.tasks),
            ('standard', 'low', 'standard', 'standard', 'protected', 'standard', 'standard', 'standard', 'standard', 'standard'),
        )
        self.assertEqual(
            tuple(task.manifest.grader.runtime for task in loaded.tasks),
            ("python3.12",) * 10,
        )
        self.assertEqual(
            tuple(task.manifest.grader.network_mode for task in loaded.tasks),
            ("disabled",) * 10,
        )
        self.assertEqual(
            tuple(task.fixture_bundle.file_count for task in loaded.tasks),
            (2, 2, 2, 3, 1, 1, 2, 1, 2, 5),
        )
        self.assertTrue(all(task.grader_bundle.file_count == 1 for task in loaded.tasks))

        protected = loaded.tasks[4].manifest.protected_authorization
        self.assertIsNotNone(protected)
        self.assertEqual(protected.actor, "shiroku46")
        self.assertEqual(protected.source, "issue_body")
        self.assertEqual(protected.required_marker, "FOUNDATION_EVAL_PROTECTED_OK")
        self.assertTrue(protected.expected_head_required)

        provider = loaded.tasks[6].manifest
        self.assertEqual(provider.category, "provider_unavailable")
        self.assertEqual(provider.expected_completion_class, "change_required")
        self.assertIsNone(provider.expected_human_action_reason)

        genuine_human = loaded.tasks[7].manifest
        self.assertEqual(genuine_human.category, "human_only")
        self.assertEqual(genuine_human.expected_completion_class, "human_action_required")
        self.assertEqual(
            genuine_human.expected_human_action_reason,
            "HUMAN_ONLY_DISCONNECTED_INTEGRATION_RECONNECTION_UI_REQUIRED",
        )

        false_human = loaded.tasks[8].manifest
        self.assertEqual(false_human.category, "human_only")
        self.assertEqual(false_human.expected_completion_class, "change_required")
        self.assertIsNone(false_human.expected_human_action_reason)

        resume = loaded.tasks[9].manifest
        self.assertEqual(resume.category, "handoff_resume")
        self.assertEqual(resume.expected_completion_class, "change_required")

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

    def test_change_tasks_reject_baselines_and_accept_known_solutions(self):
        change_tasks = [
            task
            for task in self.loaded.tasks
            if task.manifest.expected_completion_class == "change_required"
        ]
        self.assertEqual(len(change_tasks), 8)
        for task in change_tasks:
            with self.subTest(task=task.entry.task_id), tempfile.TemporaryDirectory() as temp:
                temp_root = Path(temp)
                workspace = temp_root / "workspace"
                shutil.copytree(SUITE_ROOT / task.entry.fixture_root, workspace)
                baseline = self._run_grader(task, workspace, temp_root / "baseline.json")
                self.assertEqual(baseline.outcome, "failed")

                for relative, content in SOLUTIONS[task.entry.task_id].items():
                    destination = workspace / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_text(content, encoding="utf-8")
                solved = self._run_grader(task, workspace, temp_root / "solved.json")
                self.assertEqual(solved.outcome, "passed")

    def test_stale_evidence_task_accepts_baseline_and_rejects_mutation(self):
        task = self.loaded.tasks[5]
        self.assertEqual(task.entry.task_id, "foundation.task-006")
        with tempfile.TemporaryDirectory() as temp:
            temp_root = Path(temp)
            workspace = temp_root / "workspace"
            shutil.copytree(SUITE_ROOT / task.entry.fixture_root, workspace)
            baseline = self._run_grader(task, workspace, temp_root / "baseline.json")
            self.assertEqual(baseline.outcome, "passed")
            for relative, content in STALE_MUTATION.items():
                (workspace / relative).write_text(content, encoding="utf-8")
            mutated = self._run_grader(task, workspace, temp_root / "mutated.json")
            self.assertEqual(mutated.outcome, "failed")

    def test_genuine_human_task_accepts_unchanged_baseline_and_rejects_mutation(self):
        task = self.loaded.tasks[7]
        self.assertEqual(task.entry.task_id, "foundation.task-008")
        self.assertEqual(task.manifest.expected_completion_class, "human_action_required")
        with tempfile.TemporaryDirectory() as temp:
            temp_root = Path(temp)
            workspace = temp_root / "workspace"
            shutil.copytree(SUITE_ROOT / task.entry.fixture_root, workspace)
            baseline = self._run_grader(task, workspace, temp_root / "baseline.json")
            self.assertEqual(baseline.outcome, "passed")
            for relative, content in HUMAN_MUTATION.items():
                (workspace / relative).write_text(content, encoding="utf-8")
            mutated = self._run_grader(task, workspace, temp_root / "mutated.json")
            self.assertEqual(mutated.outcome, "failed")

    def test_handoff_resume_fixture_is_exact_sha_valid(self):
        task = self.loaded.tasks[9]
        root = SUITE_ROOT / task.entry.fixture_root / ".ai-dev"
        bundle = parse_handoff_bundle(
            (root / "task-state.json").read_bytes(),
            (root / "decisions.jsonl").read_bytes(),
            (root / "handoff.md").read_bytes(),
            expected_repository=HANDOFF_REPOSITORY,
            expected_issue_number=HANDOFF_ISSUE,
            expected_base_sha=HANDOFF_BASE_SHA,
            expected_candidate_sha=HANDOFF_CANDIDATE_SHA,
        )
        self.assertEqual(bundle.state.phase, "implementation")
        self.assertEqual(bundle.active_decision_ids, ("resume-decision-001",))
        self.assertEqual(
            bundle.handoff.next_action,
            "Change src/resume.py checkpoint from partial to ready.",
        )
        self.assertEqual(bundle.state.changed_paths, ())


if __name__ == "__main__":
    unittest.main()

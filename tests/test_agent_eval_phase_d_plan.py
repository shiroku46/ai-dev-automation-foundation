"""Bind the initial Phase D trial matrix to the checked-in 30-task suite."""
from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from scripts.agent_eval_experiment_contract import parse_evaluation_experiment_plan
from scripts.agent_eval_suite_contract import load_evaluation_suite

ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "evaluation/experiments/phase-d-initial.json"
SUITE_ROOT = ROOT / "evaluation/initial"
CATALOG_PATH = SUITE_ROOT / "catalog.json"
PLAN_SHA256 = "d94e2d2eb3ad5a5731ea659b28fa9ccc66ac913c99c79639eeecf27531caa476"
CATALOG_SHA256 = "a19fc4ed329c8bc3d76e8870196c10947a1c3d176d32ba70673f3a8194064f37"
FOUNDATION_SHA = "8a5d2bd7df3401657752415abe2069b0bea291d8"


class PhaseDInitialPlanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan_raw = PLAN_PATH.read_bytes()
        cls.plan = parse_evaluation_experiment_plan(cls.plan_raw)
        cls.suite = load_evaluation_suite(CATALOG_PATH.read_bytes(), SUITE_ROOT)

    def test_plan_identity_and_matrix_size_are_exact(self):
        self.assertEqual(hashlib.sha256(self.plan_raw).hexdigest(), PLAN_SHA256)
        self.assertEqual(self.plan.plan_sha256, PLAN_SHA256)
        self.assertEqual(self.plan.experiment_id, "foundation.phase-d.initial")
        self.assertEqual(self.plan.suite_id, "foundation.initial")
        self.assertEqual(self.plan.suite_version, 7)
        self.assertEqual(self.plan.catalog_sha256, CATALOG_SHA256)
        self.assertEqual(self.plan.foundation_sha, FOUNDATION_SHA)
        self.assertEqual(self.plan.environment_profile, "ubuntu-24.04-python3.12-v1")
        self.assertEqual(self.plan.trial_count, 3)
        self.assertEqual(len(self.plan.task_ids), 30)
        self.assertEqual(len(self.plan.arms), 3)
        self.assertEqual(self.plan.expected_run_count, 270)
        self.assertEqual(
            tuple((arm.arm_id, arm.role, arm.harness, arm.adapter, arm.model) for arm in self.plan.arms),
            (
                ("baseline", "baseline", "github-direct-v1", "github-direct", None),
                ("evaluator", "evaluator", "evaluator-v1", "github-direct", None),
                ("planner", "planner", "planner-v1", "github-direct", None),
            ),
        )
        for arm in self.plan.arms:
            self.assertEqual(len(self.plan.task_ids) * self.plan.trial_count, 90)

    def test_plan_is_mechanically_bound_to_checked_in_suite(self):
        self.assertEqual(self.suite.catalog.suite_id, self.plan.suite_id)
        self.assertEqual(self.suite.catalog.suite_version, self.plan.suite_version)
        self.assertEqual(self.suite.catalog.catalog_sha256, self.plan.catalog_sha256)
        self.assertEqual(self.suite.catalog.foundation_sha, self.plan.foundation_sha)
        self.assertEqual(
            tuple(task.entry.task_id for task in self.suite.tasks),
            self.plan.task_ids,
        )
        for task in self.suite.tasks:
            with self.subTest(task=task.entry.task_id):
                self.assertEqual(task.manifest.trial_count, self.plan.trial_count)
                self.assertEqual(task.manifest.environment_profile, self.plan.environment_profile)

    def test_interruption_subset_is_exact_and_handoff_scoped(self):
        self.assertEqual(
            self.plan.interruption_task_ids,
            ("foundation.task-010", "foundation.task-015", "foundation.task-030"),
        )
        categories = {
            task.entry.task_id: task.manifest.category
            for task in self.suite.tasks
            if task.entry.task_id in self.plan.interruption_task_ids
        }
        self.assertEqual(
            categories,
            {
                "foundation.task-010": "handoff_resume",
                "foundation.task-015": "handoff_resume",
                "foundation.task-030": "handoff_resume",
            },
        )


if __name__ == "__main__":
    unittest.main()

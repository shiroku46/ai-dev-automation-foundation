"""Regression tests for the Phase D experiment-plan contract."""
from __future__ import annotations

import json
import unittest

from scripts.agent_eval_experiment_contract import (
    EvaluationExperimentError,
    parse_evaluation_experiment_plan,
)

FOUNDATION_SHA = "1" * 40
CATALOG_SHA = "2" * 64


def plan_value():
    return {
        "schema_version": 1,
        "experiment_id": "foundation.phase-d.initial",
        "suite_id": "foundation.initial",
        "suite_version": 7,
        "catalog_sha256": CATALOG_SHA,
        "foundation_sha": FOUNDATION_SHA,
        "environment_profile": "ubuntu-24.04-python3.12-v1",
        "task_ids": ["foundation.task-001", "foundation.task-010"],
        "trial_count": 2,
        "arms": [
            {
                "arm_id": "baseline",
                "role": "baseline",
                "harness": "github-direct-v1",
                "adapter": "github-direct",
                "model": None,
            },
            {
                "arm_id": "planner",
                "role": "planner",
                "harness": "planner-v1",
                "adapter": "github-direct",
                "model": None,
            },
        ],
        "interruption_task_ids": ["foundation.task-010"],
    }


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class EvaluationExperimentContractTest(unittest.TestCase):
    def parse(self, value=None):
        return parse_evaluation_experiment_plan(canonical(plan_value() if value is None else value))

    def test_valid_plan_is_immutable_and_counts_expected_runs(self):
        parsed = self.parse()
        self.assertEqual(parsed.experiment_id, "foundation.phase-d.initial")
        self.assertEqual(parsed.expected_run_count, 8)
        self.assertEqual(tuple(arm.arm_id for arm in parsed.arms), ("baseline", "planner"))
        self.assertEqual(parsed.interruption_task_ids, ("foundation.task-010",))
        with self.assertRaisesRegex(Exception, "cannot assign"):
            parsed.trial_count = 3

    def test_noncanonical_unknown_duplicate_and_schema_inputs_fail_closed(self):
        value = plan_value()
        with self.assertRaises(EvaluationExperimentError):
            parse_evaluation_experiment_plan(json.dumps(value, indent=2))
        changed = plan_value()
        changed["extra"] = True
        with self.assertRaises(EvaluationExperimentError):
            self.parse(changed)
        duplicate = canonical(plan_value()).decode()[:-1] + ',"trial_count":2}'
        with self.assertRaises(EvaluationExperimentError):
            parse_evaluation_experiment_plan(duplicate)
        changed = plan_value()
        changed["schema_version"] = 2
        with self.assertRaises(EvaluationExperimentError):
            self.parse(changed)

    def test_tasks_must_be_sorted_unique_and_interruptions_must_be_in_suite(self):
        cases = (
            ["foundation.task-010", "foundation.task-001"],
            ["foundation.task-001", "foundation.task-001"],
        )
        for tasks in cases:
            changed = plan_value()
            changed["task_ids"] = tasks
            with self.subTest(tasks=tasks), self.assertRaises(EvaluationExperimentError):
                self.parse(changed)
        changed = plan_value()
        changed["interruption_task_ids"] = ["foundation.task-999"]
        with self.assertRaises(EvaluationExperimentError):
            self.parse(changed)

    def test_arm_roles_ids_and_execution_identities_are_unique(self):
        changed = plan_value()
        changed["arms"][1]["role"] = "baseline"
        with self.assertRaises(EvaluationExperimentError):
            self.parse(changed)
        changed = plan_value()
        changed["arms"][1]["arm_id"] = "baseline"
        with self.assertRaises(EvaluationExperimentError):
            self.parse(changed)
        changed = plan_value()
        changed["arms"][1]["harness"] = "github-direct-v1"
        with self.assertRaises(EvaluationExperimentError):
            self.parse(changed)
        changed = plan_value()
        changed["arms"] = list(reversed(changed["arms"]))
        with self.assertRaises(EvaluationExperimentError):
            self.parse(changed)

    def test_identity_counts_sha_and_digest_validation_fail_closed(self):
        mutations = (
            ("trial_count", 0),
            ("suite_version", True),
            ("foundation_sha", "A" * 40),
            ("catalog_sha256", "0" * 63),
            ("environment_profile", "bad profile"),
        )
        for key, value in mutations:
            changed = plan_value()
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(EvaluationExperimentError):
                self.parse(changed)


if __name__ == "__main__":
    unittest.main()

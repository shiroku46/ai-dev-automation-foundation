"""Regression tests for the sealed agent-visible evaluation request boundary."""
from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path

from scripts.agent_eval_experiment_contract import parse_evaluation_experiment_plan
from scripts.agent_eval_suite_contract import EvaluationSuite, ValidatedSuiteTask, load_evaluation_suite
from scripts.agent_eval_trial_request import (
    MAX_REQUEST_BYTES,
    AgentTrialRequestError,
    agent_trial_request_sha256,
    build_agent_trial_request,
    serialize_agent_trial_request,
)

ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "evaluation/experiments/phase-d-initial.json"
SUITE_ROOT = ROOT / "evaluation/initial"
CATALOG_PATH = SUITE_ROOT / "catalog.json"


def replace_task(suite: EvaluationSuite, task_id: str, **changes) -> EvaluationSuite:
    tasks = []
    for task in suite.tasks:
        if task.entry.task_id == task_id:
            tasks.append(replace(task, **changes))
        else:
            tasks.append(task)
    return replace(suite, tasks=tuple(tasks))


class AgentTrialRequestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = parse_evaluation_experiment_plan(PLAN_PATH.read_bytes())
        cls.suite = load_evaluation_suite(CATALOG_PATH.read_bytes(), SUITE_ROOT)

    def build(self, arm="baseline", task="foundation.task-001", trial=1, *, plan=None, suite=None):
        return build_agent_trial_request(
            self.plan if plan is None else plan,
            self.suite if suite is None else suite,
            arm,
            task,
            trial,
        )

    def test_request_is_minimal_immutable_canonical_and_deterministic(self):
        first = self.build()
        second = self.build()
        first_raw = serialize_agent_trial_request(first)
        second_raw = serialize_agent_trial_request(second)
        self.assertEqual(first, second)
        self.assertEqual(first_raw, second_raw)
        self.assertEqual(first_raw, json.dumps(json.loads(first_raw), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        self.assertEqual(agent_trial_request_sha256(first), agent_trial_request_sha256(second))
        self.assertEqual(first.task_id, "foundation.task-001")
        self.assertEqual(first.fixture_bundle.file_count, 2)
        self.assertEqual(tuple(item.path for item in first.fixture_bundle.files), tuple(sorted(item.path for item in first.fixture_bundle.files)))
        with self.assertRaisesRegex(Exception, "cannot assign"):
            first.trial = 2

    def test_arm_task_and_trial_change_request_identity(self):
        identities = {
            agent_trial_request_sha256(self.build("baseline", "foundation.task-001", 1)),
            agent_trial_request_sha256(self.build("planner", "foundation.task-001", 1)),
            agent_trial_request_sha256(self.build("baseline", "foundation.task-002", 1)),
            agent_trial_request_sha256(self.build("baseline", "foundation.task-001", 2)),
        }
        self.assertEqual(len(identities), 4)

    def test_all_planned_requests_exclude_grader_and_ground_truth_keys(self):
        prohibited_keys = {
            "grader",
            "grader_root",
            "grader_sha256",
            "entrypoint",
            "runtime",
            "timeout_seconds",
            "expected_completion_class",
            "expected_human_action_reason",
        }

        def keys(value):
            result = set()
            if isinstance(value, dict):
                result.update(value)
                for item in value.values():
                    result.update(keys(item))
            elif isinstance(value, list):
                for item in value:
                    result.update(keys(item))
            return result

        for arm in self.plan.arms:
            for task_id in self.plan.task_ids:
                request = self.build(arm.arm_id, task_id, 1)
                raw = serialize_agent_trial_request(request)
                data = json.loads(raw)
                with self.subTest(arm=arm.arm_id, task=task_id):
                    self.assertFalse(keys(data) & prohibited_keys)
                    self.assertNotIn("min(upper, max(value, lower))", raw.decode("utf-8"))
                    self.assertNotIn("KNOWN_SOLUTION", raw.decode("utf-8"))
                    self.assertEqual(set(data["fixture_bundle"]["files"][0]), {"executable", "path", "sha256", "size"})

    def test_protected_authorization_is_exposed_only_as_trusted_metadata(self):
        request = self.build(task="foundation.task-005")
        self.assertEqual(request.risk_tier, "protected")
        self.assertIsNotNone(request.protected_authorization)
        self.assertEqual(request.protected_authorization.actor, "shiroku46")
        self.assertTrue(request.protected_authorization.expected_head_required)
        raw = json.loads(serialize_agent_trial_request(request))
        self.assertEqual(
            set(raw["protected_authorization"]),
            {"actor", "source", "required_marker", "expected_head_required"},
        )
        ordinary = json.loads(serialize_agent_trial_request(self.build()))
        self.assertIsNone(ordinary["protected_authorization"])

    def test_plan_suite_arm_task_and_trial_mismatches_fail_closed(self):
        bad_plan = replace(self.plan, foundation_sha="f" * 40)
        with self.assertRaises(AgentTrialRequestError):
            self.build(plan=bad_plan)
        bad_plan = replace(self.plan, catalog_sha256="0" * 64)
        with self.assertRaises(AgentTrialRequestError):
            self.build(plan=bad_plan)
        for arm, task, trial in (
            ("missing", "foundation.task-001", 1),
            ("baseline", "foundation.task-999", 1),
            ("baseline", "foundation.task-001", 0),
            ("baseline", "foundation.task-001", 4),
        ):
            with self.subTest(arm=arm, task=task, trial=trial), self.assertRaises(AgentTrialRequestError):
                self.build(arm, task, trial)

    def test_task_trial_environment_bundle_and_protected_invariants_fail_closed(self):
        task = next(item for item in self.suite.tasks if item.entry.task_id == "foundation.task-001")
        manifest = replace(task.manifest, trial_count=2)
        with self.assertRaises(AgentTrialRequestError):
            self.build(suite=replace_task(self.suite, task.entry.task_id, manifest=manifest))
        manifest = replace(task.manifest, environment_profile="other-profile")
        with self.assertRaises(AgentTrialRequestError):
            self.build(suite=replace_task(self.suite, task.entry.task_id, manifest=manifest))
        fixture_identity = replace(task.manifest.fixture_bundle, sha256="0" * 64)
        manifest = replace(task.manifest, fixture_bundle=fixture_identity)
        with self.assertRaises(AgentTrialRequestError):
            self.build(suite=replace_task(self.suite, task.entry.task_id, manifest=manifest))

        protected = next(item for item in self.suite.tasks if item.entry.task_id == "foundation.task-005")
        manifest = replace(protected.manifest, protected_authorization=None)
        bad_suite = replace_task(self.suite, protected.entry.task_id, manifest=manifest)
        with self.assertRaises(AgentTrialRequestError):
            self.build(task=protected.entry.task_id, suite=bad_suite)

    def test_request_serialization_is_bounded(self):
        request = self.build()
        oversized = replace(request, issue_body="x" * MAX_REQUEST_BYTES)
        with self.assertRaises(AgentTrialRequestError):
            serialize_agent_trial_request(oversized)


if __name__ == "__main__":
    unittest.main()

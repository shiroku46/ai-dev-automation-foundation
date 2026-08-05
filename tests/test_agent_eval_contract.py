"""Strict coding-agent evaluation run contract tests."""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from scripts.agent_eval_contract import (
    CHECK_CONCLUSIONS,
    CHECK_KEYS,
    ENVIRONMENT_KEYS,
    FAILURE_CLASSES,
    HANDOFF_RECOVERY_STATES,
    MAX_RECORD_BYTES,
    METRIC_KEYS,
    NETWORK_MODES,
    OUTCOMES,
    TOP_LEVEL_KEYS,
    EvaluationRunError,
    parse_evaluation_run,
)

ROOT = Path(__file__).resolve().parents[1]
FOUNDATION_SHA = "1" * 40
CANDIDATE_SHA = "2" * 40


def valid_payload():
    return {
        "schema_version": 1,
        "run_id": "baseline.task-001.trial-1",
        "task_id": "task-001",
        "foundation_sha": FOUNDATION_SHA,
        "candidate_sha": CANDIDATE_SHA,
        "harness": "github-direct-v1",
        "adapter": "github-direct",
        "model": None,
        "trial": 1,
        "environment": {
            "os": "ubuntu-24.04",
            "architecture": "x86_64",
            "python": "3.12.0",
            "cpu_count": 2,
            "memory_mib": 4096,
            "timeout_seconds": 900,
            "network_mode": "disabled",
            "tool_versions": {"python": "3.12.0", "git": "2.50.0"},
        },
        "started_at": "2026-08-05T00:00:00Z",
        "finished_at": "2026-08-05T00:01:30Z",
        "outcome": "passed",
        "failure_class": None,
        "metrics": {
            "task_success": True,
            "first_pass_success": True,
            "scope_violation_attempts": 0,
            "regression_escapes": 0,
            "human_action_requests": 0,
            "confirmed_human_actions": 0,
            "false_human_action_requests": 0,
            "iterations": 1,
            "elapsed_seconds": 90,
            "github_api_requests": 12,
            "actions_minutes": 1.5,
            "estimated_cost_usd": None,
            "handoff_recovery": "not_applicable",
        },
        "checks": [
            {
                "name": "Unit Tests",
                "source": ".github/workflows/unit-tests.yml",
                "required": True,
                "conclusion": "success",
                "head_sha": CANDIDATE_SHA,
            },
            {
                "name": "CI",
                "source": ".github/workflows/ci.yml",
                "required": True,
                "conclusion": "success",
                "head_sha": CANDIDATE_SHA,
            },
        ],
        "unresolved_review_threads": 0,
    }


class EvaluationRunContractTest(unittest.TestCase):
    def parse(self, payload):
        return parse_evaluation_run(json.dumps(payload, sort_keys=True))

    def assert_invalid(self, payload):
        with self.assertRaises(EvaluationRunError):
            self.parse(payload)

    def test_valid_record_is_immutable_and_deterministic(self):
        parsed = self.parse(valid_payload())
        self.assertEqual(parsed.run_id, "baseline.task-001.trial-1")
        self.assertEqual(parsed.candidate_sha, CANDIDATE_SHA)
        self.assertEqual(
            parsed.environment.tool_versions,
            (("git", "2.50.0"), ("python", "3.12.0")),
        )
        self.assertEqual([item.name for item in parsed.checks], ["CI", "Unit Tests"])
        with self.assertRaisesRegex(Exception, "cannot assign"):
            parsed.trial = 2

    def test_malformed_size_schema_and_unknown_keys_fail_closed(self):
        for content in (b"", b"not-json", b"[]", b"\xff"):
            with self.subTest(content=content):
                with self.assertRaises(EvaluationRunError):
                    parse_evaluation_run(content)
        with self.assertRaises(EvaluationRunError):
            parse_evaluation_run(b"x" * (MAX_RECORD_BYTES + 1))

        payload = valid_payload()
        payload["schema_version"] = 2
        self.assert_invalid(payload)
        payload = valid_payload()
        payload["extra"] = True
        self.assert_invalid(payload)
        payload = valid_payload()
        del payload["trial"]
        self.assert_invalid(payload)

    def test_identity_sha_and_scalar_boundaries_fail_closed(self):
        mutations = (
            ("run_id", "Uppercase"),
            ("task_id", "../task"),
            ("foundation_sha", "A" * 40),
            ("candidate_sha", "2" * 39),
            ("harness", "bad harness"),
            ("adapter", ""),
            ("trial", True),
            ("trial", 0),
            ("model", " bad"),
        )
        for key, value in mutations:
            payload = valid_payload()
            payload[key] = value
            with self.subTest(key=key, value=value):
                self.assert_invalid(payload)

    def test_environment_schema_and_limits_fail_closed(self):
        payload = valid_payload()
        payload["environment"]["extra"] = True
        self.assert_invalid(payload)

        for key, value in (
            ("cpu_count", True),
            ("cpu_count", 0),
            ("memory_mib", 0),
            ("timeout_seconds", 0),
            ("network_mode", "connected"),
            ("tool_versions", {"Bad Tool": "1"}),
            ("tool_versions", {"git": " bad"}),
        ):
            payload = valid_payload()
            payload["environment"][key] = value
            with self.subTest(key=key, value=value):
                self.assert_invalid(payload)

    def test_timestamp_and_elapsed_invariants_fail_closed(self):
        payload = valid_payload()
        payload["started_at"] = "2026-08-05T00:00:00+00:00"
        self.assert_invalid(payload)

        payload = valid_payload()
        payload["finished_at"] = "2026-08-04T23:59:59Z"
        self.assert_invalid(payload)

        payload = valid_payload()
        payload["metrics"]["elapsed_seconds"] = 88
        self.assert_invalid(payload)

    def test_first_pass_and_numeric_metric_invariants_fail_closed(self):
        payload = valid_payload()
        payload["metrics"]["iterations"] = 2
        self.assert_invalid(payload)

        payload = valid_payload()
        payload["metrics"]["task_success"] = False
        self.assert_invalid(payload)

        for key, value in (
            ("iterations", True),
            ("iterations", 0),
            ("scope_violation_attempts", -1),
            ("elapsed_seconds", float("inf")),
            ("actions_minutes", -1),
            ("estimated_cost_usd", -1),
            ("handoff_recovery", "unknown"),
        ):
            payload = valid_payload()
            payload["metrics"][key] = value
            with self.subTest(key=key, value=value):
                self.assert_invalid(payload)

    def test_human_request_invariants_fail_closed(self):
        payload = valid_payload()
        payload["metrics"].update({
            "human_action_requests": 1,
            "confirmed_human_actions": 1,
            "false_human_action_requests": 1,
        })
        self.assert_invalid(payload)

        for confirmed, false in ((2, 0), (0, 2)):
            payload = valid_payload()
            payload["metrics"].update({
                "human_action_requests": 1,
                "confirmed_human_actions": confirmed,
                "false_human_action_requests": false,
            })
            with self.subTest(confirmed=confirmed, false=false):
                self.assert_invalid(payload)

    def test_exact_head_and_duplicate_check_evidence_fail_closed(self):
        payload = valid_payload()
        payload["checks"][0]["head_sha"] = "3" * 40
        self.assert_invalid(payload)

        payload = valid_payload()
        payload["checks"].append(copy.deepcopy(payload["checks"][0]))
        self.assert_invalid(payload)

        payload = valid_payload()
        payload["checks"][0]["extra"] = True
        self.assert_invalid(payload)

        payload = valid_payload()
        payload["checks"][0]["required"] = 1
        self.assert_invalid(payload)

    def test_passed_outcome_requires_clean_required_evidence(self):
        payload = valid_payload()
        payload["checks"] = []
        self.assert_invalid(payload)

        payload = valid_payload()
        payload["checks"][0]["conclusion"] = "failure"
        self.assert_invalid(payload)

        payload = valid_payload()
        payload["unresolved_review_threads"] = 1
        self.assert_invalid(payload)

        payload = valid_payload()
        payload["failure_class"] = "model"
        self.assert_invalid(payload)

        payload = valid_payload()
        payload["checks"][0]["required"] = False
        payload["checks"][0]["conclusion"] = "neutral"
        self.parse(payload)

    def test_non_passed_outcome_requires_failure_class_and_task_failure(self):
        for outcome in ("failed", "blocked", "infra_error"):
            payload = valid_payload()
            payload["outcome"] = outcome
            payload["failure_class"] = "infrastructure"
            payload["metrics"]["task_success"] = False
            payload["metrics"]["first_pass_success"] = False
            with self.subTest(outcome=outcome):
                self.parse(payload)

        payload = valid_payload()
        payload["outcome"] = "failed"
        payload["metrics"]["task_success"] = False
        payload["metrics"]["first_pass_success"] = False
        self.assert_invalid(payload)

        payload = valid_payload()
        payload["outcome"] = "failed"
        payload["failure_class"] = "model"
        self.assert_invalid(payload)

    def test_public_schema_tracks_parser_keys_and_enums(self):
        schema = json.loads(
            (ROOT / "docs/AGENT_EVAL_RUN.schema.json").read_text(encoding="utf-8")
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(TOP_LEVEL_KEYS))
        self.assertEqual(set(schema["properties"]), set(TOP_LEVEL_KEYS))
        self.assertEqual(
            set(schema["$defs"]["environment"]["required"]),
            set(ENVIRONMENT_KEYS),
        )
        self.assertEqual(
            set(schema["$defs"]["metrics"]["required"]), set(METRIC_KEYS)
        )
        self.assertEqual(
            set(schema["$defs"]["check"]["required"]), set(CHECK_KEYS)
        )
        self.assertEqual(set(schema["properties"]["outcome"]["enum"]), set(OUTCOMES))
        failure_enum = schema["properties"]["failure_class"]["oneOf"][1]["enum"]
        self.assertEqual(set(failure_enum), set(FAILURE_CLASSES))
        self.assertEqual(
            set(schema["$defs"]["environment"]["properties"]["network_mode"]["enum"]),
            set(NETWORK_MODES),
        )
        self.assertEqual(
            set(schema["$defs"]["metrics"]["properties"]["handoff_recovery"]["enum"]),
            set(HANDOFF_RECOVERY_STATES),
        )
        self.assertEqual(
            set(schema["$defs"]["check"]["properties"]["conclusion"]["enum"]),
            set(CHECK_CONCLUSIONS),
        )


if __name__ == "__main__":
    unittest.main()

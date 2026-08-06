"""Immutable coding-agent evaluation task manifest tests."""
from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

from scripts.agent_eval_task_contract import (
    AUTHORIZATION_SOURCES,
    BUNDLE_KEYS,
    CATEGORIES,
    EXPECTED_COMPLETION_CLASSES,
    GRADER_KEYS,
    GRADER_RUNTIMES,
    HUMAN_ONLY_REASON_CODES,
    ISSUE_KEYS,
    MAX_MANIFEST_BYTES,
    NETWORK_MODES,
    PROTECTED_AUTHORIZATION_KEYS,
    RISK_TIERS,
    TOP_LEVEL_KEYS,
    EvaluationTaskError,
    parse_evaluation_task,
)

ROOT = Path(__file__).resolve().parents[1]


def canonical(payload: dict) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def valid_payload() -> dict:
    return {
        "schema_version": 1,
        "task_id": "foundation.task-001",
        "task_version": 1,
        "category": "bug_fix",
        "risk_tier": "standard",
        "fixture_bundle": {
            "sha256": "1" * 64,
            "file_count": 12,
            "uncompressed_bytes": 4096,
        },
        "grader": {
            "sha256": "2" * 64,
            "runtime": "python3.12",
            "entrypoint": "grader/grade.py",
            "timeout_seconds": 900,
            "network_mode": "disabled",
        },
        "issue": {
            "title": "[Eval] Repair bounded parser behavior",
            "body": "## Goal\n\nRepair the bounded parser and preserve exact-path scope.",
        },
        "allowed_paths": ["src/parser.py", "tests/**"],
        "prohibited_effects": [
            "No workflow changes",
            "No credential access",
        ],
        "required_checks": ["CI", "Unit Tests"],
        "protected_authorization": None,
        "expected_completion_class": "change_required",
        "expected_human_action_reason": None,
        "trial_count": 3,
        "environment_profile": "ubuntu-24.04-python3.12-v1",
        "tags": ["parser", "bounded", "regression"],
    }


class EvaluationTaskContractTest(unittest.TestCase):
    def parse(self, payload: dict):
        return parse_evaluation_task(canonical(payload))

    def assert_invalid(self, payload: dict):
        with self.assertRaises(EvaluationTaskError):
            self.parse(payload)

    def test_valid_manifest_is_immutable_and_digest_bound(self):
        raw = canonical(valid_payload())
        parsed = parse_evaluation_task(raw)
        self.assertEqual(parsed.task_id, "foundation.task-001")
        self.assertEqual(parsed.allowed_paths, ("src/parser.py", "tests/**"))
        self.assertEqual(parsed.manifest_sha256, hashlib.sha256(raw).hexdigest())
        with self.assertRaisesRegex(Exception, "cannot assign"):
            parsed.task_version = 2

    def test_manifest_must_be_canonical_utf8_json(self):
        payload = valid_payload()
        noncanonical = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
        with self.assertRaises(EvaluationTaskError):
            parse_evaluation_task(noncanonical)
        with self.assertRaises(EvaluationTaskError):
            parse_evaluation_task(canonical(payload) + b"\n")
        for content in (
            b"", b"[]", b"not-json", b"\xff",
            b'{"schema_version":1,"schema_version":1}',
            b'{"x":NaN}',
        ):
            with self.subTest(content=content):
                with self.assertRaises(EvaluationTaskError):
                    parse_evaluation_task(content)
        with self.assertRaises(EvaluationTaskError):
            parse_evaluation_task(b"x" * (MAX_MANIFEST_BYTES + 1))

    def test_schema_version_unknown_missing_and_duplicate_keys_fail(self):
        for version in (2, True):
            payload = valid_payload()
            payload["schema_version"] = version
            with self.subTest(version=version):
                self.assert_invalid(payload)
        payload = valid_payload()
        payload["unknown"] = True
        self.assert_invalid(payload)
        payload = valid_payload()
        del payload["trial_count"]
        self.assert_invalid(payload)
        for key in ("fixture_bundle", "grader", "issue"):
            payload = valid_payload()
            payload[key]["unknown"] = True
            with self.subTest(key=key):
                self.assert_invalid(payload)

    def test_identity_version_category_risk_and_environment_fail_closed(self):
        mutations = (
            ("task_id", "Uppercase"),
            ("task_id", "../task"),
            ("task_version", True),
            ("task_version", 0),
            ("category", "feature"),
            ("risk_tier", "high"),
            ("trial_count", 0),
            ("trial_count", 101),
            ("environment_profile", "ubuntu profile"),
        )
        for key, value in mutations:
            payload = valid_payload()
            payload[key] = value
            with self.subTest(key=key, value=value):
                self.assert_invalid(payload)

    def test_fixture_bundle_digest_and_bounds_fail_closed(self):
        mutations = (
            ("sha256", "A" * 64),
            ("sha256", "1" * 63),
            ("file_count", True),
            ("file_count", 0),
            ("uncompressed_bytes", 0),
        )
        for key, value in mutations:
            payload = valid_payload()
            payload["fixture_bundle"][key] = value
            with self.subTest(key=key, value=value):
                self.assert_invalid(payload)

    def test_grader_contract_is_digest_runtime_path_timeout_and_network_bound(self):
        mutations = (
            ("sha256", "A" * 64),
            ("runtime", "python"),
            ("entrypoint", "grade.py"),
            ("entrypoint", "grader/../grade.py"),
            ("entrypoint", "grader/*.py"),
            ("entrypoint", "grader/.GIT/grade.py"),
            ("entrypoint", "C:/grader/grade.py"),
            ("timeout_seconds", True),
            ("timeout_seconds", 0),
            ("network_mode", "unrestricted"),
        )
        for key, value in mutations:
            payload = valid_payload()
            payload["grader"][key] = value
            with self.subTest(key=key, value=value):
                self.assert_invalid(payload)

    def test_allowed_paths_accept_exact_paths_and_one_trailing_scope_only(self):
        valid = valid_payload()
        valid["allowed_paths"] = ["docs/guide.md", "src/module/**"]
        self.parse(valid)

        invalid_sets = (
            [],
            ["../src/file.py"],
            ["/src/file.py"],
            ["C:/src/file.py"],
            ["src\\file.py"],
            ["src//file.py"],
            [".git/config"],
            [".GIT/config"],
            ["src/.Git/config"],
            ["src/*.py"],
            ["src/**/file.py"],
            ["src/**", "tests/**"],
            ["src/file.py", "src/file.py"],
        )
        for paths in invalid_sets:
            payload = valid_payload()
            payload["allowed_paths"] = paths
            with self.subTest(paths=paths):
                self.assert_invalid(payload)

    def test_issue_effect_check_and_tag_collections_are_bounded_and_unique(self):
        payload = valid_payload()
        payload["issue"]["title"] = " bad"
        self.assert_invalid(payload)
        payload = valid_payload()
        payload["issue"]["body"] = "bad\r\nbody"
        self.assert_invalid(payload)

        for key, values in (
            ("prohibited_effects", []),
            ("prohibited_effects", ["No writes", "no writes"]),
            ("required_checks", []),
            ("required_checks", ["CI", "ci"]),
            ("tags", []),
            ("tags", ["valid", "Bad Tag"]),
            ("tags", ["same", "same"]),
        ):
            payload = valid_payload()
            payload[key] = values
            with self.subTest(key=key, values=values):
                self.assert_invalid(payload)

    def test_protected_authorization_is_complete_and_risk_bound(self):
        payload = valid_payload()
        payload["risk_tier"] = "protected"
        self.assert_invalid(payload)

        payload = valid_payload()
        payload["risk_tier"] = "protected"
        payload["protected_authorization"] = {
            "actor": "shiroku46",
            "source": "issue_body",
            "required_marker": "AUTHORIZED_PROTECTED_CHANGE",
            "expected_head_required": True,
        }
        parsed = self.parse(payload)
        self.assertTrue(parsed.protected_authorization.expected_head_required)

        payload = valid_payload()
        payload["protected_authorization"] = {
            "actor": "shiroku46",
            "source": "issue_body",
            "required_marker": "AUTHORIZED_PROTECTED_CHANGE",
            "expected_head_required": True,
        }
        self.assert_invalid(payload)

        for key, value in (
            ("actor", "bad user"),
            ("source", "email"),
            ("required_marker", "not-uppercase"),
            ("expected_head_required", False),
            ("expected_head_required", 1),
        ):
            payload = valid_payload()
            payload["risk_tier"] = "protected"
            payload["protected_authorization"] = {
                "actor": "shiroku46",
                "source": "issue_body",
                "required_marker": "AUTHORIZED_PROTECTED_CHANGE",
                "expected_head_required": True,
            }
            payload["protected_authorization"][key] = value
            with self.subTest(key=key, value=value):
                self.assert_invalid(payload)

    def test_expected_human_action_reason_agrees_with_completion_class(self):
        for reason in HUMAN_ONLY_REASON_CODES:
            payload = valid_payload()
            payload["category"] = "human_only"
            payload["expected_completion_class"] = "human_action_required"
            payload["expected_human_action_reason"] = reason
            with self.subTest(reason=reason):
                self.parse(payload)

        payload = valid_payload()
        payload["expected_completion_class"] = "human_action_required"
        self.assert_invalid(payload)

        payload = valid_payload()
        payload["expected_human_action_reason"] = next(iter(HUMAN_ONLY_REASON_CODES))
        self.assert_invalid(payload)

        payload = valid_payload()
        payload["expected_completion_class"] = "human_action_required"
        payload["expected_human_action_reason"] = "HUMAN_ONLY_GENERIC"
        self.assert_invalid(payload)

    def test_sensitive_credentials_and_hidden_reasoning_markers_fail_closed(self):
        values = (
            "github" + "_pat_" + "abcdefghijklmnopqrstuvwxyz1234567890",
            "password=correct-horse-battery-staple",
            "-----BEGIN PRIVATE KEY-----",
            "<analysis>private material</analysis>",
            "chain-of-thought: private material",
        )
        for value in values:
            payload = valid_payload()
            payload["issue"]["body"] = value
            with self.subTest(value=value):
                self.assert_invalid(payload)

    def test_public_schema_tracks_parser_keys_enums_and_nested_contracts(self):
        schema = json.loads(
            (ROOT / "docs/AGENT_EVAL_TASK.schema.json").read_text(encoding="utf-8")
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(TOP_LEVEL_KEYS))
        self.assertEqual(set(schema["properties"]), set(TOP_LEVEL_KEYS))
        self.assertEqual(
            set(schema["$defs"]["bundle"]["required"]), set(BUNDLE_KEYS)
        )
        self.assertEqual(
            set(schema["$defs"]["grader"]["required"]), set(GRADER_KEYS)
        )
        self.assertEqual(
            set(schema["$defs"]["issue"]["required"]), set(ISSUE_KEYS)
        )
        self.assertEqual(
            set(schema["$defs"]["protectedAuthorization"]["required"]),
            set(PROTECTED_AUTHORIZATION_KEYS),
        )
        self.assertEqual(set(schema["properties"]["category"]["enum"]), set(CATEGORIES))
        self.assertEqual(set(schema["properties"]["risk_tier"]["enum"]), set(RISK_TIERS))
        self.assertEqual(
            set(schema["$defs"]["grader"]["properties"]["runtime"]["enum"]),
            set(GRADER_RUNTIMES),
        )
        self.assertEqual(
            set(schema["$defs"]["grader"]["properties"]["network_mode"]["enum"]),
            set(NETWORK_MODES),
        )
        self.assertEqual(
            set(schema["$defs"]["protectedAuthorization"]["properties"]["source"]["enum"]),
            set(AUTHORIZATION_SOURCES),
        )
        self.assertEqual(
            set(schema["properties"]["expected_completion_class"]["enum"]),
            set(EXPECTED_COMPLETION_CLASSES),
        )
        human_enum = schema["properties"]["expected_human_action_reason"]["oneOf"][1]["enum"]
        self.assertEqual(set(human_enum), set(HUMAN_ONLY_REASON_CODES))

        exact_pattern = schema["$defs"]["exactPath"]["pattern"]
        allowed_pattern = schema["$defs"]["allowedPath"]["pattern"]
        grader_pattern = schema["$defs"]["grader"]["properties"]["entrypoint"]["pattern"]
        self.assertIsNotNone(re.fullmatch(exact_pattern, ".github/workflows/ci.yml"))
        self.assertIsNotNone(re.fullmatch(allowed_pattern, "tests/**"))
        self.assertIsNotNone(re.fullmatch(grader_pattern, "grader/grade.py"))
        for unsafe in (".git/config", ".GIT/config", "src/.Git/config"):
            with self.subTest(schema_path=unsafe):
                self.assertIsNone(re.fullmatch(exact_pattern, unsafe))
                self.assertIsNone(re.fullmatch(allowed_pattern, unsafe))
        for unsafe in ("grader/.git/grade.py", "grader/.GIT/grade.py"):
            with self.subTest(schema_grader=unsafe):
                self.assertIsNone(re.fullmatch(grader_pattern, unsafe))


if __name__ == "__main__":
    unittest.main()

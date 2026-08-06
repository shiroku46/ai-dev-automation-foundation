"""Canonical grader invocation and result contract tests."""
from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

from scripts.agent_eval_grader_contract import (
    CHECK_KEYS,
    MAX_RESULT_BYTES,
    OUTCOMES,
    RUNTIME_COMMANDS,
    TOP_LEVEL_KEYS,
    GraderContractError,
    GraderInfrastructureError,
    GraderResultExpectation,
    build_grader_command,
    parse_grader_result,
    validate_grader_process_result,
)

ROOT = Path(__file__).resolve().parents[1]


def canonical(value: dict) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def expectation() -> GraderResultExpectation:
    return GraderResultExpectation(
        task_id="foundation.task-001",
        task_version=1,
        manifest_sha256="1" * 64,
        grader_sha256="2" * 64,
        foundation_sha="3" * 40,
        base_sha="4" * 40,
        candidate_sha="5" * 40,
    )


def valid_payload() -> dict:
    expected = expectation()
    return {
        "schema_version": 1,
        "task_id": expected.task_id,
        "task_version": expected.task_version,
        "manifest_sha256": expected.manifest_sha256,
        "grader_sha256": expected.grader_sha256,
        "foundation_sha": expected.foundation_sha,
        "base_sha": expected.base_sha,
        "candidate_sha": expected.candidate_sha,
        "outcome": "passed",
        "checks": [
            {
                "check_id": "acceptance",
                "outcome": "passed",
                "message": "Acceptance conditions passed.",
                "evidence_paths": ["src/example.py", "tests/test_example.py"],
            }
        ],
        "summary": "The deterministic acceptance checks passed.",
    }


class GraderContractTest(unittest.TestCase):
    def parse(self, payload: dict, expected: GraderResultExpectation | None = None):
        return parse_grader_result(
            canonical(payload),
            expected=expected or expectation(),
        )

    def assert_invalid(self, payload: dict, expected=None):
        with self.assertRaises(GraderContractError):
            self.parse(payload, expected)

    def test_valid_result_is_immutable_digest_bound_and_identity_checked(self):
        raw = canonical(valid_payload())
        parsed = parse_grader_result(raw, expected=expectation())
        self.assertEqual(parsed.outcome, "passed")
        self.assertEqual(parsed.result_sha256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(
            parsed.checks[0].evidence_paths,
            ("src/example.py", "tests/test_example.py"),
        )
        with self.assertRaisesRegex(Exception, "cannot assign"):
            parsed.outcome = "failed"

    def test_result_requires_canonical_bounded_strict_json(self):
        payload = valid_payload()
        invalid_values = (
            b"",
            b"[]",
            b"not-json",
            b"\xff",
            json.dumps(payload, indent=2).encode("utf-8"),
            canonical(payload) + b"\n",
            b'{"schema_version":1,"schema_version":1}',
            b'{"x":NaN}',
            b"x" * (MAX_RESULT_BYTES + 1),
        )
        for value in invalid_values:
            with self.subTest(value=value[:40]):
                with self.assertRaises(GraderContractError):
                    parse_grader_result(value, expected=expectation())

    def test_unknown_missing_nested_and_version_fields_fail_closed(self):
        payload = valid_payload()
        payload["unknown"] = True
        self.assert_invalid(payload)
        payload = valid_payload()
        del payload["summary"]
        self.assert_invalid(payload)
        payload = valid_payload()
        payload["checks"][0]["unknown"] = True
        self.assert_invalid(payload)
        for version in (2, True):
            payload = valid_payload()
            payload["schema_version"] = version
            with self.subTest(version=version):
                self.assert_invalid(payload)

    def test_every_identity_field_rejects_stale_or_cross_task_evidence(self):
        mutations = {
            "task_id": "foundation.task-002",
            "task_version": 2,
            "manifest_sha256": "a" * 64,
            "grader_sha256": "b" * 64,
            "foundation_sha": "c" * 40,
            "base_sha": "d" * 40,
            "candidate_sha": "e" * 40,
        }
        for field, value in mutations.items():
            payload = valid_payload()
            payload[field] = value
            with self.subTest(field=field):
                self.assert_invalid(payload)
        with self.assertRaises(GraderContractError):
            parse_grader_result(canonical(valid_payload()), expected="invalid")

    def test_checks_are_nonempty_sorted_unique_and_paths_are_exact(self):
        payload = valid_payload()
        payload["checks"] = []
        self.assert_invalid(payload)

        second = {
            "check_id": "build",
            "outcome": "passed",
            "message": "Build passed.",
            "evidence_paths": [],
        }
        payload = valid_payload()
        payload["checks"] = [second, payload["checks"][0]]
        self.assert_invalid(payload)

        payload = valid_payload()
        payload["checks"].append(dict(payload["checks"][0]))
        self.assert_invalid(payload)

        invalid_paths = (
            "/absolute",
            "../escape",
            "C:/drive",
            "src\\file.py",
            "src//file.py",
            ".GIT/config",
            "src/*.py",
            "src/control\nfile.py",
            " src/file.py",
            "src/file.py ",
        )
        for path in invalid_paths:
            payload = valid_payload()
            payload["checks"][0]["evidence_paths"] = [path]
            with self.subTest(path=path):
                self.assert_invalid(payload)

        payload = valid_payload()
        payload["checks"][0]["evidence_paths"] = [
            "src/File.py",
            "src/file.py",
        ]
        self.assert_invalid(payload)

    def test_overall_outcome_must_agree_with_check_outcomes(self):
        payload = valid_payload()
        payload["outcome"] = "failed"
        self.assert_invalid(payload)

        payload = valid_payload()
        payload["checks"][0]["outcome"] = "failed"
        self.assert_invalid(payload)

        payload = valid_payload()
        payload["outcome"] = "failed"
        payload["checks"][0]["outcome"] = "failed"
        self.assertEqual(self.parse(payload).outcome, "failed")

    def test_exit_code_and_result_outcome_agreement_is_deterministic(self):
        passed = self.parse(valid_payload())
        self.assertIs(validate_grader_process_result(0, passed), passed)

        payload = valid_payload()
        payload["outcome"] = "failed"
        payload["checks"][0]["outcome"] = "failed"
        failed = self.parse(payload)
        self.assertIs(validate_grader_process_result(1, failed), failed)

        for exit_code, result in (
            (0, None),
            (0, failed),
            (1, passed),
            (2, failed),
            (-9, None),
            (True, passed),
        ):
            with self.subTest(exit_code=exit_code, result=result):
                with self.assertRaises(GraderInfrastructureError):
                    validate_grader_process_result(exit_code, result)

    def test_sensitive_credentials_and_hidden_reasoning_fail_closed(self):
        values = (
            "github" + "_pat_" + "abcdefghijklmnopqrstuvwxyz1234567890",
            "pass" + "word=" + "not-a-real-secret-value",
            "-----BEGIN " + "PRIVATE KEY-----",
            "<" + "analysis>private material</" + "analysis>",
            "chain-of-" + "thought: private material",
        )
        for value in values:
            payload = valid_payload()
            payload["summary"] = value
            with self.subTest(value=value):
                self.assert_invalid(payload)

    def test_command_builder_returns_exact_argv_without_shell_interpolation(self):
        command = build_grader_command(
            "python3.12",
            "grader/grade.py",
            "/tmp/workspace with spaces",
            "/tmp/results/result.json",
        )
        self.assertEqual(
            command,
            (
                "python3.12",
                "grader/grade.py",
                "--workspace",
                "/tmp/workspace with spaces",
                "--result",
                "/tmp/results/result.json",
            ),
        )
        invalid_commands = (
            ("python", "grader/grade.py", "/tmp/work", "/tmp/result"),
            ("bash", "../grade.sh", "/tmp/work", "/tmp/result"),
            ("bash", "grader/.GIT/grade.sh", "/tmp/work", "/tmp/result"),
            ("bash", "grader/control\n/grade.sh", "/tmp/work", "/tmp/result"),
            ("bash", "grader/grade.sh", "relative", "/tmp/result"),
            ("bash", "grader/grade.sh", "/tmp/work/../work", "/tmp/result"),
            ("bash", "grader/grade.sh", "/tmp/work", "/tmp/work/result"),
        )
        for runtime, entrypoint, workspace, result in invalid_commands:
            with self.subTest(runtime=runtime, entrypoint=entrypoint):
                with self.assertRaises(GraderContractError):
                    build_grader_command(runtime, entrypoint, workspace, result)

    def test_public_schema_tracks_parser_keys_enums_and_path_rules(self):
        schema = json.loads(
            (ROOT / "docs/AGENT_EVAL_GRADER_RESULT.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(TOP_LEVEL_KEYS))
        self.assertEqual(set(schema["properties"]), set(TOP_LEVEL_KEYS))
        self.assertEqual(
            set(schema["$defs"]["check"]["required"]),
            set(CHECK_KEYS),
        )
        self.assertEqual(
            set(schema["properties"]["outcome"]["enum"]),
            set(OUTCOMES),
        )
        self.assertEqual(
            set(schema["$defs"]["check"]["properties"]["outcome"]["enum"]),
            set(OUTCOMES),
        )
        evidence_pattern = schema["$defs"]["evidencePath"]["pattern"]
        self.assertIsNotNone(re.fullmatch(evidence_pattern, "src/example.py"))
        for invalid in (
            "../escape",
            ".GIT/config",
            "src/*.py",
            "src//file.py",
            "src/control\nfile.py",
            " src/file.py",
            "src/file.py ",
        ):
            with self.subTest(invalid=invalid):
                self.assertIsNone(re.fullmatch(evidence_pattern, invalid))
        self.assertEqual(
            set(RUNTIME_COMMANDS),
            {"python3.12", "python3.13", "node20", "bash"},
        )


if __name__ == "__main__":
    unittest.main()

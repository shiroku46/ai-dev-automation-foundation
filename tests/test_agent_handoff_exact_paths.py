"""Focused regression tests for exact handoff repository paths."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from scripts.agent_handoff_contract import HandoffContractError, parse_handoff_bundle
from tests.test_agent_handoff_contract import (
    BASE_SHA,
    CANDIDATE_SHA,
    ISSUE,
    REPOSITORY,
    valid_bundle,
)

ROOT = Path(__file__).resolve().parents[1]
UNSAFE_PATHS = (
    "tests/**",
    "src/?odule.py",
    "src/[abc].py",
    "src/{a,b}.py",
    "C:/secret.txt",
    ".git/config",
    "nested/.GIT/index",
    "/absolute/path",
    "../outside",
    "a/../../outside",
    "a\\b.py",
    "a//b.py",
)


class AgentHandoffExactPathTest(unittest.TestCase):
    def parse(self, state_raw, decisions_raw, handoff_raw):
        return parse_handoff_bundle(
            state_raw,
            decisions_raw,
            handoff_raw,
            expected_repository=REPOSITORY,
            expected_issue_number=ISSUE,
            expected_base_sha=BASE_SHA,
            expected_candidate_sha=CANDIDATE_SHA,
        )

    def test_literal_repository_paths_are_accepted(self):
        state, _, decisions_raw, handoff_raw = valid_bundle()
        state["read_paths"] = ["src/parser.py", ".github/workflows/ci.yml"]
        state["changed_paths"] = ["tests/test_parser.py"]
        parsed = self.parse(
            json.dumps(state, sort_keys=True).encode("utf-8"),
            decisions_raw,
            handoff_raw,
        )
        self.assertEqual(parsed.state.changed_paths, ("tests/test_parser.py",))

    def test_globs_drive_paths_git_metadata_and_traversal_are_rejected(self):
        for unsafe_path in UNSAFE_PATHS:
            state, _, decisions_raw, handoff_raw = valid_bundle()
            state["changed_paths"] = [unsafe_path]
            with self.subTest(path=unsafe_path), self.assertRaises(HandoffContractError):
                self.parse(
                    json.dumps(state, sort_keys=True).encode("utf-8"),
                    decisions_raw,
                    handoff_raw,
                )

    def test_public_schema_rejects_the_same_unsafe_path_families(self):
        schema = json.loads(
            (ROOT / "docs/AGENT_HANDOFF.schema.json").read_text(encoding="utf-8")
        )
        pattern = re.compile(schema["$defs"]["path"]["pattern"])
        self.assertIsNotNone(pattern.fullmatch("src/parser.py"))
        self.assertIsNotNone(pattern.fullmatch(".github/workflows/ci.yml"))
        for unsafe_path in UNSAFE_PATHS:
            with self.subTest(path=unsafe_path):
                self.assertIsNone(pattern.fullmatch(unsafe_path))


if __name__ == "__main__":
    unittest.main()

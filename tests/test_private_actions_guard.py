"""Tests for private-target GitHub Actions cost guarding."""
from __future__ import annotations

import unittest
from pathlib import Path

from scripts.private_actions_guard import (
    FOUNDATION_WORKFLOW_PATHS,
    PRIVATE_ACTIONS_GUARD_EXPRESSION,
    PrivateActionsGuardError,
    guard_private_actions_workflow,
    guarded_jobs,
    validate_private_actions_workflow,
)

ROOT = Path(__file__).resolve().parents[1]


class PrivateActionsGuardTest(unittest.TestCase):
    def test_all_managed_source_workflows_transform_and_validate(self):
        for relative in FOUNDATION_WORKFLOW_PATHS:
            with self.subTest(relative=relative):
                source = (ROOT / relative).read_bytes()
                guarded = guard_private_actions_workflow(source)
                self.assertNotEqual(guarded, source)
                validate_private_actions_workflow(guarded)
                jobs = guarded_jobs(guarded)
                self.assertGreater(len(jobs), 0)
                for job in jobs:
                    self.assertIn(PRIVATE_ACTIONS_GUARD_EXPRESSION, job.condition)

    def test_existing_single_line_condition_is_preserved_and_combined(self):
        source = b"""name: x\non:\n  workflow_dispatch:\njobs:\n  one:\n    if: ${{ always() && github.ref == 'refs/heads/main' }}\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo one\n  two:\n    if: success()\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo two\n"""
        guarded = guard_private_actions_workflow(source)
        text = guarded.decode()
        self.assertIn("always() && github.ref == 'refs/heads/main'", text)
        self.assertIn("success()", text)
        self.assertEqual(text.count("FOUNDATION_PRIVATE_ACTIONS_ENABLED"), 2)
        validate_private_actions_workflow(guarded)

    def test_job_without_condition_gets_guard(self):
        source = b"""name: x\non:\n  pull_request:\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo test\n"""
        guarded = guard_private_actions_workflow(source)
        self.assertIn(
            f"    if: ${{{{ {PRIVATE_ACTIONS_GUARD_EXPRESSION} }}}}\n".encode(),
            guarded,
        )

    def test_transform_is_idempotent(self):
        source = b"""name: x\non:\n  pull_request:\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo test\n"""
        once = guard_private_actions_workflow(source)
        twice = guard_private_actions_workflow(once)
        self.assertEqual(twice, once)

    def test_multiline_or_ambiguous_conditions_fail_closed(self):
        for condition in ("|", ">", "|-", ">-"):
            source = (
                "name: x\non:\n  workflow_dispatch:\njobs:\n  test:\n"
                f"    if: {condition}\n"
                "      always()\n"
                "    runs-on: ubuntu-latest\n"
            ).encode()
            with self.subTest(condition=condition):
                with self.assertRaises(PrivateActionsGuardError):
                    guard_private_actions_workflow(source)

    def test_missing_jobs_and_malformed_utf8_fail_closed(self):
        with self.assertRaises(PrivateActionsGuardError):
            guard_private_actions_workflow(b"name: no-jobs\n")
        with self.assertRaises(PrivateActionsGuardError):
            guard_private_actions_workflow(b"\xff")


if __name__ == "__main__":
    unittest.main()

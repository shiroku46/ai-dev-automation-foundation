import importlib
import os
import sys
import unittest
from unittest.mock import patch

from scripts.supervisor_policy import is_protected, protected_scope_is_authorized

DEFAULT_SHA = "d" * 40
ISSUE_NUMBER = 12


class QueueRecoveryFinalRaceTest(unittest.TestCase):
    def _load(self):
        environment = {
            "REPOSITORY": "example/foundation-e2e",
            "DEFAULT_BRANCH": "main",
            "AUTOMATION_OWNER": "owner",
        }
        with patch.dict(os.environ, environment, clear=False):
            for name in (
                "scripts.supervisor_queue_recovery_v3",
                "scripts.supervisor_queue_recovery_v2",
                "scripts.supervisor_queue_recovery",
                "scripts.supervisor_runtime",
            ):
                sys.modules.pop(name, None)
            return importlib.import_module("scripts.supervisor_queue_recovery_v3")

    def test_every_queue_recovery_module_is_independently_protected(self):
        paths = [
            "scripts/supervisor_queue_recovery.py",
            "scripts/supervisor_queue_recovery_v2.py",
            "scripts/supervisor_queue_recovery_v3.py",
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertTrue(is_protected(path))

        ordinary_only = """
## Allowed paths
- scripts/supervisor_queue_recovery.py
- scripts/supervisor_queue_recovery_v2.py
- scripts/supervisor_queue_recovery_v3.py
"""
        self.assertFalse(protected_scope_is_authorized(paths, ordinary_only))

    def test_alternative_appearing_during_connected_audit_fails_closed(self):
        module = self._load()
        scope = {
            "declared_paths": ["tests/**"],
            "protected_authorized_paths": [],
        }
        with patch.object(module, "_exact_default_sha", return_value=DEFAULT_SHA), patch.object(
            module, "_validated_issue_scope", return_value=scope
        ), patch.object(
            module, "_trusted_alternative_candidates", side_effect=[[], [77]]
        ) as alternatives, patch.object(
            module,
            "_original_connected_exhaustion_snapshot",
            return_value={"completed": True, "default_sha": DEFAULT_SHA},
        ) as original:
            with self.assertRaisesRegex(RuntimeError, "appeared during"):
                module.complete_connected_exhaustion_snapshot(
                    ISSUE_NUMBER,
                    "fingerprint",
                    DEFAULT_SHA,
                    ["retry-1-started.json"],
                )
        self.assertEqual(alternatives.call_count, 2)
        original.assert_called_once()

    def test_stable_empty_alternative_set_is_recorded_after_final_recheck(self):
        module = self._load()
        scope = {
            "declared_paths": ["tests/**"],
            "protected_authorized_paths": [],
        }
        with patch.object(module, "_exact_default_sha", return_value=DEFAULT_SHA), patch.object(
            module, "_validated_issue_scope", return_value=scope
        ), patch.object(
            module, "_trusted_alternative_candidates", side_effect=[[], []]
        ) as alternatives, patch.object(
            module,
            "_original_connected_exhaustion_snapshot",
            return_value={"completed": True, "default_sha": DEFAULT_SHA},
        ):
            snapshot = module.complete_connected_exhaustion_snapshot(
                ISSUE_NUMBER,
                "fingerprint",
                DEFAULT_SHA,
                ["retry-1-started.json"],
            )
        self.assertEqual(alternatives.call_count, 2)
        self.assertEqual(snapshot["alternative_candidate_prs"], [])
        self.assertTrue(snapshot["alternative_paths_exhausted"])
        self.assertTrue(snapshot["source_issue_authorization_verified"])


if __name__ == "__main__":
    unittest.main()

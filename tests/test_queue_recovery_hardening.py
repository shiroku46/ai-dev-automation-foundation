import importlib
import json
import os
import sys
import unittest
from unittest.mock import patch

DEFAULT_SHA = "d" * 40


class QueueRecoveryHardeningTest(unittest.TestCase):
    def _load(self):
        environment = {
            "REPOSITORY": "example/foundation-e2e",
            "DEFAULT_BRANCH": "main",
            "AUTOMATION_OWNER": "owner",
            "GITHUB_RUN_ID": "9001",
        }
        with patch.dict(os.environ, environment, clear=False):
            for name in (
                "scripts.supervisor_queue_recovery_v2",
                "scripts.supervisor_queue_recovery",
                "scripts.supervisor_runtime",
            ):
                sys.modules.pop(name, None)
            return importlib.import_module("scripts.supervisor_queue_recovery_v2")

    def test_unstarted_retry_intent_is_not_counted(self):
        module = self._load()
        records = [
            "retry-1.json",
            "retry-1-started.json",
            "retry-2.json",
            "other.json",
        ]
        with patch.object(module, "_original_list_records", return_value=records):
            self.assertEqual(
                module.started_attempt_records("root"),
                ["other.json", "retry-1-started.json", "retry-1.json"],
            )

    def test_dispatch_run_requires_fixed_identity_and_default_sha(self):
        module = self._load()
        valid = {
            "id": 101,
            "event": "workflow_dispatch",
            "head_branch": "main",
            "head_sha": DEFAULT_SHA,
            "path": ".github/workflows/claude-queue.yml@main",
            "repository": {"full_name": "example/foundation-e2e"},
            "actor": {"login": "github-actions[bot]"},
        }
        moved = dict(valid, id=102, head_sha="e" * 40)
        with patch.object(module.recovery, "_queue_runs", return_value=[moved, valid]):
            self.assertEqual(
                module._matching_dispatch_runs(set(), DEFAULT_SHA),
                [valid],
            )

    def test_exhaustion_requires_two_identical_connected_snapshots(self):
        module = self._load()
        snapshots = [
            {"completed": True, "default_sha": DEFAULT_SHA},
            {"completed": True, "default_sha": DEFAULT_SHA},
        ]
        with patch.object(module.runtime, "current_default_sha", return_value=DEFAULT_SHA), patch.object(
            module, "_connected_exhaustion_snapshot", side_effect=snapshots
        ) as audit, patch.object(module.recovery, "_put_exact_record", return_value=True) as record:
            self.assertTrue(
                module.guarded_record_exhaustion(
                    12,
                    "fingerprint",
                    ["retry-1.json", "retry-2.json", "retry-3.json"],
                )
            )
        self.assertEqual(audit.call_count, 2)
        payload = json.loads(record.call_args.args[1])
        self.assertIs(payload["notification"], False)
        self.assertEqual(payload["audit"], snapshots[1])

    def test_changed_connected_snapshot_fails_closed(self):
        module = self._load()
        with patch.object(module.runtime, "current_default_sha", return_value=DEFAULT_SHA), patch.object(
            module,
            "_connected_exhaustion_snapshot",
            side_effect=[
                {"completed": True, "default_sha": DEFAULT_SHA},
                {"completed": False, "default_sha": DEFAULT_SHA},
            ],
        ), patch.object(module.recovery, "_put_exact_record") as record:
            with self.assertRaisesRegex(RuntimeError, "changed between live passes"):
                module.guarded_record_exhaustion(
                    12,
                    "fingerprint",
                    ["retry-1.json", "retry-2.json", "retry-3.json"],
                )
        record.assert_not_called()


if __name__ == "__main__":
    unittest.main()

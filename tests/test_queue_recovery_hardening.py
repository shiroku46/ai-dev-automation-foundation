import importlib
import json
import os
import sys
import unittest
from unittest.mock import Mock, patch

DEFAULT_SHA = "d" * 40
FINGERPRINT = "a" * 20


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
        records = ["retry-1.json", "retry-1-started.json", "retry-2.json", "other.json"]
        with patch.object(module, "_original_list_records", return_value=records):
            self.assertEqual(module.started_attempt_records("root"), ["other.json", "retry-1-started.json", "retry-1.json"])

    def test_dispatch_run_requires_fixed_attempt_identity_and_default_sha(self):
        module = self._load()
        title = module._attempt_run_title(12, FINGERPRINT, 1)
        valid = {
            "id": 101,
            "event": "workflow_dispatch",
            "head_branch": "main",
            "head_sha": DEFAULT_SHA,
            "display_title": title,
            "path": ".github/workflows/claude-queue.yml@main",
            "repository": {"full_name": "example/foundation-e2e"},
            "actor": {"login": "github-actions[bot]"},
        }
        moved = dict(valid, id=102, head_sha="e" * 40)
        wrong_attempt = dict(valid, id=103, display_title=module._attempt_run_title(12, FINGERPRINT, 2))
        with patch.object(module.recovery, "_queue_runs", return_value=[moved, wrong_attempt, valid]):
            self.assertEqual(module._matching_dispatch_runs(12, FINGERPRINT, 1, DEFAULT_SHA), [valid])

    def test_existing_intent_reconciles_exact_run_without_redispatch(self):
        module = self._load()
        started_path = "root/retry-1-started.json"
        with patch.object(module, "_intent_identity", return_value=(DEFAULT_SHA, "root/retry-1.json", started_path, False)), patch.object(
            module, "_record_payload", return_value=None
        ), patch.object(module, "_wait_for_existing_attempt_run", return_value=501), patch.object(
            module, "_wait_for_queue_implementation_start", return_value=501
        ), patch.object(module, "_record_started", return_value=True) as started, patch.object(
            module, "_dispatch_fixed_retry"
        ) as dispatch:
            self.assertTrue(module.guarded_dispatch_retry(12, FINGERPRINT, 1))
        dispatch.assert_not_called()
        started.assert_called_once_with(12, FINGERPRINT, 1, DEFAULT_SHA, 501, started_path)

    def test_new_intent_dispatches_exact_identity_inputs(self):
        module = self._load()
        started_path = "root/retry-1-started.json"
        dispatch = Mock()
        with patch.object(module, "_intent_identity", return_value=(DEFAULT_SHA, "root/retry-1.json", started_path, True)), patch.object(
            module, "_record_payload", return_value=None
        ), patch.object(module, "_dispatch_fixed_retry", dispatch), patch.object(
            module, "_wait_for_queue_implementation_start", return_value=502
        ), patch.object(module, "_record_started", return_value=True):
            self.assertTrue(module.guarded_dispatch_retry(12, FINGERPRINT, 1))
        dispatch.assert_called_once_with(12, FINGERPRINT, 1, DEFAULT_SHA)

    def test_exhaustion_requires_two_identical_connected_snapshots(self):
        module = self._load()
        snapshots = [{"completed": True, "default_sha": DEFAULT_SHA}, {"completed": True, "default_sha": DEFAULT_SHA}]
        with patch.object(module.runtime, "current_default_sha", return_value=DEFAULT_SHA), patch.object(
            module, "_connected_exhaustion_snapshot", side_effect=snapshots
        ) as audit, patch.object(module.recovery, "_put_exact_record", return_value=True) as record:
            self.assertTrue(module.guarded_record_exhaustion(12, FINGERPRINT, ["retry-1.json", "retry-2.json", "retry-3.json"]))
        self.assertEqual(audit.call_count, 2)
        payload = json.loads(record.call_args.args[1])
        self.assertIs(payload["notification"], False)
        self.assertEqual(payload["audit"], snapshots[1])

    def test_changed_connected_snapshot_fails_closed(self):
        module = self._load()
        with patch.object(module.runtime, "current_default_sha", return_value=DEFAULT_SHA), patch.object(
            module, "_connected_exhaustion_snapshot", side_effect=[{"completed": True}, {"completed": False}]
        ), patch.object(module.recovery, "_put_exact_record") as record:
            with self.assertRaisesRegex(RuntimeError, "changed between live passes"):
                module.guarded_record_exhaustion(12, FINGERPRINT, ["retry-1.json", "retry-2.json", "retry-3.json"])
        record.assert_not_called()


if __name__ == "__main__":
    unittest.main()

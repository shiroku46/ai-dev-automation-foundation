import importlib
import os
import sys
import unittest
from unittest.mock import patch


DEFAULT_SHA = "d" * 40
FINGERPRINT = "a" * 20


class QueueRecoveryTerminalBeforeStartTest(unittest.TestCase):
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

    def test_terminal_prepare_conclusions_are_persisted_not_started(self):
        module = self._load()
        for conclusion in ("failure", "cancelled", "skipped", "timed_out"):
            with self.subTest(conclusion=conclusion), patch.object(
                module,
                "_intent_identity",
                return_value=(DEFAULT_SHA, "root/retry-1.json", "root/retry-1-started.json", False),
            ), patch.object(module, "_record_payload", return_value=None), patch.object(
                module, "_wait_for_existing_attempt_run", return_value=701
            ), patch.object(
                module, "_reconcile_attempt_run", return_value=("terminal", 701, conclusion)
            ), patch.object(
                module, "_record_terminal_before_start", return_value=True
            ) as terminal, patch.object(module, "_record_started") as started, patch.object(
                module, "_dispatch_fixed_retry"
            ) as dispatch:
                self.assertTrue(module.guarded_dispatch_retry(12, FINGERPRINT, 1))
            terminal.assert_called_once_with(12, FINGERPRINT, 1, DEFAULT_SHA, 701, conclusion)
            started.assert_not_called()
            dispatch.assert_not_called()

    def test_queued_and_in_progress_runs_remain_nonterminal(self):
        module = self._load()
        for status in ("queued", "in_progress"):
            run = {"id": 801, "status": status}
            with self.subTest(status=status), patch.object(
                module, "QUEUE_START_TIMEOUT_SECONDS", 1
            ), patch.object(module, "_matching_dispatch_runs", return_value=[run]), patch.object(
                module.runtime, "api_key_pages", return_value=[]
            ), patch.object(module.time, "monotonic", side_effect=[0, 0, 1]), patch.object(
                module.time, "sleep"
            ):
                with self.assertRaisesRegex(RuntimeError, "did not resolve"):
                    module._reconcile_attempt_run(12, FINGERPRINT, 1, DEFAULT_SHA)

    def test_repeated_terminal_reconciliation_does_not_redispatch_or_recount(self):
        module = self._load()
        terminal = {
            "attempt": 1,
            "default_sha": DEFAULT_SHA,
            "expected_run_title": module._attempt_run_title(12, FINGERPRINT, 1),
            "fixed_workflow": module.recovery.QUEUE_WORKFLOW_FILE,
            "issue_number": 12,
            "notification": False,
            "prepare_conclusion": "timed_out",
            "queue_run_id": 901,
            "request_fingerprint": FINGERPRINT,
            "trusted_workflow_path": module.recovery.QUEUE_WORKFLOW_PATH,
        }
        with patch.object(
            module, "_intent_identity", return_value=(DEFAULT_SHA, "intent", "started", False)
        ), patch.object(module, "_record_payload", side_effect=[None, terminal]), patch.object(
            module, "_dispatch_fixed_retry"
        ) as dispatch, patch.object(module, "_record_terminal_before_start") as record:
            self.assertFalse(module.guarded_dispatch_retry(12, FINGERPRINT, 1))
        dispatch.assert_not_called()
        record.assert_not_called()

        records = ["retry-1.json", "retry-1-terminal.json"]
        with patch.object(module, "_original_list_records", return_value=records):
            first = module.started_attempt_records("root")
            second = module.started_attempt_records("root")
        self.assertEqual(first, second)
        self.assertEqual(first.count("retry-1.json"), 1)

    def test_terminal_attempt_progresses_to_next_attempt_and_bounded_exhaustion(self):
        module = self._load()
        records = ["retry-1.json", "retry-1-terminal.json"]
        with patch.object(module, "_original_list_records", return_value=records):
            visible = module.started_attempt_records("root")
        retry_records = [name for name in visible if name == "retry-1.json"]
        self.assertEqual(len(retry_records) + 1, 2)

        records.extend(
            [
                "retry-2.json",
                "retry-2-terminal.json",
                "retry-3.json",
                "retry-3-terminal.json",
            ]
        )
        with patch.object(module, "_original_list_records", return_value=records):
            visible = module.started_attempt_records("root")
        retry_records = [name for name in visible if name in {"retry-1.json", "retry-2.json", "retry-3.json"}]
        self.assertEqual(len(retry_records), module.recovery.MAX_QUEUE_RECOVERY_ATTEMPTS)


if __name__ == "__main__":
    unittest.main()

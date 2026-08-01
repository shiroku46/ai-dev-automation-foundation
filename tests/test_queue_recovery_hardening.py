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
        records = ["retry-1.json", "retry-1-terminal.json", "retry-2.json", "other.json"]
        with patch.object(module, "_original_list_records", return_value=records):
            self.assertEqual(module.started_attempt_records("root"), ["other.json", "retry-1-terminal.json", "retry-1.json"])

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
            module, "_reconcile_attempt_run", return_value=("started", 501, None)
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
        ), patch.object(module, "_wait_for_existing_attempt_run", return_value=None), patch.object(module, "_dispatch_fixed_retry", dispatch), patch.object(
            module, "_reconcile_attempt_run", return_value=("started", 502, None)
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

    def test_connected_exhaustion_accepts_timed_out_prepare(self):
        module = self._load()
        issue_number = 12
        retry_records = ["retry-1.json", "retry-2.json", "retry-3.json"]
        records = retry_records + [
            "retry-1-terminal.json",
            "retry-2-terminal.json",
            "retry-3-terminal.json",
        ]

        def api(path):
            if path == f"repos/{module.runtime.REPO}":
                return {"full_name": module.runtime.REPO, "default_branch": "main"}
            if "/actions/workflows/" in path:
                filename = path.rsplit("/", 1)[-1]
                workflow_paths = {
                    module.recovery.QUEUE_WORKFLOW_FILE: module.recovery.QUEUE_WORKFLOW_PATH,
                    "ci-reconcile.yml": ".github/workflows/ci-reconcile.yml",
                    "supervisor.yml": ".github/workflows/supervisor.yml",
                }
                return {"path": workflow_paths[filename], "state": "active"}
            run_id = int(path.rsplit("/", 1)[-1])
            attempt = run_id - 700
            return {
                "repository": {"full_name": module.runtime.REPO},
                "path": f"{module.recovery.QUEUE_WORKFLOW_PATH}@main",
                "event": "workflow_dispatch",
                "head_branch": "main",
                "head_sha": DEFAULT_SHA,
                "display_title": module._attempt_run_title(issue_number, FINGERPRINT, attempt),
                "status": "completed",
            }

        def payload(path):
            attempt = int(path.split("retry-")[1].split("-")[0])
            return {
                "attempt": attempt,
                "default_sha": DEFAULT_SHA,
                "expected_run_title": module._attempt_run_title(issue_number, FINGERPRINT, attempt),
                "fixed_workflow": module.recovery.QUEUE_WORKFLOW_FILE,
                "issue_number": issue_number,
                "notification": False,
                "prepare_conclusion": "timed_out",
                "queue_run_id": 700 + attempt,
                "request_fingerprint": FINGERPRINT,
                "trusted_workflow_path": module.recovery.QUEUE_WORKFLOW_PATH,
            }

        queue_text = "\n".join((
            "workflow_dispatch:", "trusted_supervisor:", "trusted_run_id:",
            "request_fingerprint:", "recovery_attempt:",
            "run-name: Claude Issue Queue issue-",
            'expected_path = f".github/workflows/ci-reconcile.yml@{default_branch}"',
            "permissions:",
        ))
        reconcile_text = "\n".join((
            'workflows: ["CI", "Unit Tests", "Claude Issue Queue"]',
            "queue_recovery:", "actions: write", "contents: write", "issues: read",
            "pull-requests: read", "python -m scripts.supervisor_queue_recovery_v3",
        ))
        supervisor_text = "\n".join((
            "actions: read", "checks: read", "contents: write", "issues: write",
            "pull-requests: write", "python -m scripts.supervisor_final_guard",
        ))
        with patch.object(module.runtime, "api", side_effect=api), patch.object(
            module.runtime, "api_key_pages",
            return_value=[{"name": "prepare", "status": "completed", "conclusion": "timed_out"}],
        ), patch.object(module.runtime, "current_default_sha", return_value=DEFAULT_SHA), patch.object(
            module.recovery, "_revalidate_request"
        ), patch.object(module.recovery, "_active_queue_run_exists", return_value=False), patch.object(
            module, "_fetch_text", side_effect=[queue_text, reconcile_text, supervisor_text]
        ), patch.object(module, "_original_list_records", return_value=records), patch.object(
            module, "_record_payload", side_effect=payload
        ):
            snapshot = module._connected_exhaustion_snapshot(
                issue_number, FINGERPRINT, DEFAULT_SHA, retry_records
            )
        self.assertTrue(snapshot["completed"])
        self.assertEqual(
            [item["prepare_conclusion"] for item in snapshot["run_evidence"]],
            ["timed_out"] * 3,
        )


if __name__ == "__main__":
    unittest.main()

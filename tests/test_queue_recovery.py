import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class QueueRecoveryTest(unittest.TestCase):
    def _load(self, *, event_name="schedule", event_payload=None):
        environment = {
            "REPOSITORY": "example/foundation-e2e",
            "DEFAULT_BRANCH": "main",
            "AUTOMATION_OWNER": "owner",
            "GITHUB_EVENT_NAME": event_name,
            "GITHUB_RUN_ID": "9001",
        }
        if event_payload is not None:
            temp = tempfile.TemporaryDirectory()
            self.addCleanup(temp.cleanup)
            event_path = Path(temp.name) / "event.json"
            event_path.write_text(json.dumps(event_payload), encoding="utf-8")
            environment["GITHUB_EVENT_PATH"] = str(event_path)
        with patch.dict(os.environ, environment, clear=False):
            sys.modules.pop("scripts.supervisor_queue_recovery", None)
            sys.modules.pop("scripts.supervisor_runtime", None)
            module = importlib.import_module("scripts.supervisor_queue_recovery")
        module._test_environment = environment
        return module

    @staticmethod
    def _env(module):
        return patch.dict(os.environ, module._test_environment, clear=False)

    @staticmethod
    def _issue(number=12, *, author="owner", body="/claude-run\n\nDo work"):
        return {
            "number": number,
            "state": "open",
            "body": body,
            "created_at": "2026-08-01T00:00:00Z",
            "user": {"login": author},
        }

    def test_only_failed_same_repository_queue_completion_is_admitted(self):
        payload = {
            "workflow_run": {
                "name": "Claude Issue Queue",
                "path": ".github/workflows/claude-queue.yml@main",
                "head_branch": "main",
                "status": "completed",
                "conclusion": "failure",
                "event": "issues",
                "repository": {"full_name": "example/foundation-e2e"},
                "actor": {"login": "owner"},
            }
        }
        recovery = self._load(event_name="workflow_run", event_payload=payload)
        with self._env(recovery):
            self.assertTrue(recovery._event_allows_recovery())

        payload["workflow_run"]["name"] = "CI"
        recovery = self._load(event_name="workflow_run", event_payload=payload)
        with self._env(recovery):
            self.assertFalse(recovery._event_allows_recovery())

        payload["workflow_run"]["name"] = "Claude Issue Queue"
        payload["workflow_run"]["conclusion"] = "success"
        recovery = self._load(event_name="workflow_run", event_payload=payload)
        with self._env(recovery):
            self.assertFalse(recovery._event_allows_recovery())

    def test_dispatches_one_fixed_retry_for_trusted_request_without_pr(self):
        recovery = self._load()
        issue = self._issue()
        dispatch = Mock(return_value="")
        with self._env(recovery), patch.object(
            recovery, "_event_allows_recovery", return_value=True
        ), patch.object(
            recovery, "_active_queue_run_exists", return_value=False
        ), patch.object(
            recovery.runtime,
            "api_list",
            side_effect=[[issue], []],
        ), patch.object(
            recovery, "_list_records", return_value=[]
        ), patch.object(
            recovery, "_put_exact_record", return_value=True
        ) as record, patch.object(
            recovery, "_revalidate_request", return_value=issue
        ), patch.object(
            recovery.runtime, "current_default_sha", return_value="d" * 40
        ), patch.object(
            recovery.runtime, "gh", dispatch
        ):
            self.assertEqual(recovery.reconcile(), 0)

        self.assertTrue(record.called)
        dispatch.assert_called_once_with(
            "workflow",
            "run",
            "claude-queue.yml",
            "--repo",
            "example/foundation-e2e",
            "--ref",
            "main",
            "-f",
            "issue_number=12",
            "-f",
            "trusted_supervisor=true",
            "-f",
            "trusted_run_id=9001",
        )

    def test_existing_retry_intent_is_idempotent(self):
        recovery = self._load()
        issue = self._issue()
        with self._env(recovery), patch.object(
            recovery, "_event_allows_recovery", return_value=True
        ), patch.object(
            recovery, "_active_queue_run_exists", return_value=False
        ), patch.object(
            recovery.runtime,
            "api_list",
            side_effect=[[issue], []],
        ), patch.object(
            recovery, "_list_records", return_value=[]
        ), patch.object(
            recovery, "_put_exact_record", return_value=False
        ), patch.object(
            recovery.runtime, "current_default_sha", return_value="d" * 40
        ), patch.object(recovery.runtime, "gh") as dispatch:
            self.assertEqual(recovery.reconcile(), 0)
        dispatch.assert_not_called()

    def test_retry_exhaustion_is_non_notifying_and_does_not_dispatch(self):
        recovery = self._load()
        issue = self._issue()
        records = ["retry-1.json", "retry-2.json", "retry-3.json"]
        with self._env(recovery), patch.object(
            recovery, "_event_allows_recovery", return_value=True
        ), patch.object(
            recovery, "_active_queue_run_exists", return_value=False
        ), patch.object(
            recovery.runtime,
            "api_list",
            side_effect=[[issue], []],
        ), patch.object(
            recovery, "_list_records", return_value=records
        ), patch.object(
            recovery, "_put_exact_record", return_value=True
        ) as record, patch.object(recovery.runtime, "gh") as dispatch:
            self.assertEqual(recovery.reconcile(), 0)
        dispatch.assert_not_called()
        content = record.call_args.args[1]
        payload = json.loads(content)
        self.assertEqual(payload["reason"], "QUEUE_PIPELINE_RETRY_EXHAUSTED")
        self.assertIs(payload["notification"], False)
        self.assertEqual(payload["retry_records"], records)

    def test_active_queue_run_suppresses_recovery(self):
        recovery = self._load()
        with self._env(recovery), patch.object(
            recovery, "_event_allows_recovery", return_value=True
        ), patch.object(
            recovery, "_active_queue_run_exists", return_value=True
        ), patch.object(recovery.runtime, "api_list") as issues:
            self.assertEqual(recovery.reconcile(), 0)
        issues.assert_not_called()

    def test_untrusted_issue_is_ignored(self):
        recovery = self._load()
        issue = self._issue(author="outsider")
        with self._env(recovery), patch.object(
            recovery, "_event_allows_recovery", return_value=True
        ), patch.object(
            recovery, "_active_queue_run_exists", return_value=False
        ), patch.object(
            recovery.runtime,
            "api_list",
            side_effect=[[issue], []],
        ), patch.object(recovery, "_dispatch_retry") as dispatch:
            self.assertEqual(recovery.reconcile(), 0)
        dispatch.assert_not_called()

    def test_source_contains_no_public_failure_notification_or_label_mutation(self):
        recovery = self._load()
        source = Path(recovery.__file__).read_text(encoding="utf-8")
        self.assertNotIn("issue comment", source)
        self.assertNotIn("issue edit", source)
        self.assertNotIn("ai-blocked", source)
        self.assertIn('"notification": False', source)


if __name__ == "__main__":
    unittest.main()

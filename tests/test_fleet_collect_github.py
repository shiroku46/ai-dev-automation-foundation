"""Tests for the read-only exact-head Fleet Progress GitHub collector."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from scripts.fleet_collect_github import (
    FleetCollectorError,
    GitHubApi,
    collect_document,
    main,
    resolve_token,
    validate_config,
)
from scripts.fleet_progress import validate_document

SHA = "a" * 40
OLD_SHA = "b" * 40
NOW = datetime(2026, 8, 4, 6, 0, 0, tzinfo=timezone.utc)


def project(**overrides):
    value = {
        "repository": "owner/example",
        "phase": "Fleet dashboard",
        "issue": 157,
        "pull_request": 158,
        "required_workflows": ["CI", "Unit Tests"],
        "implementation_route": "github-direct",
        "risk_tier": "standard",
        "selected_auditor": "codex",
        "audit_state": "pending",
        "next_action": "Complete one exact-SHA audit",
        "blocker": None,
        "human_action_required": False,
        "baseline_status": None,
    }
    value.update(overrides)
    return value


def config(*projects):
    return {"schema_version": 1, "projects": list(projects)}


def pull(*, state="open", merged=False, draft=True, sha=SHA):
    return {
        "number": 158,
        "state": state,
        "merged": merged,
        "merged_at": "2026-08-04T05:50:00Z" if merged else None,
        "draft": draft,
        "head": {"sha": sha, "repo": {"full_name": "owner/example"}},
        "updated_at": "2026-08-04T05:55:00Z",
    }


def run(
    name,
    *,
    status="completed",
    conclusion="success",
    updated="2026-08-04T05:56:00Z",
    run_id=1,
    sha=SHA,
):
    return {
        "id": run_id,
        "name": name,
        "event": "pull_request",
        "head_sha": sha,
        "status": status,
        "conclusion": conclusion,
        "updated_at": updated,
    }


class FakeApi:
    def __init__(self, pull_data, runs):
        self.pull_data = pull_data
        self.runs = {"total_count": len(runs), "workflow_runs": runs}
        self.calls = []

    def get_pull(self, repository, pull_request):
        self.calls.append(("pull", repository, pull_request))
        return self.pull_data

    def get_workflow_runs(self, repository, head_sha):
        self.calls.append(("runs", repository, head_sha))
        return self.runs


class FakeResponse:
    def __init__(self, value):
        self.payload = json.dumps(value).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, _limit):
        return self.payload


class FleetCollectorTest(unittest.TestCase):
    def collect(self, cfg, pull_data=None, runs=None):
        projects = validate_config(config(cfg))
        api = FakeApi(
            pull_data or pull(),
            runs or [run("CI"), run("Unit Tests", run_id=2)],
        )
        document = collect_document(projects, api, now=lambda: NOW)
        validate_document(document)
        return document, api

    def test_draft_success_with_pending_audit_is_review_required(self):
        document, api = self.collect(project())
        record = document["projects"][0]
        self.assertEqual(record["status"], "review_required")
        self.assertEqual(record["head_sha"], SHA)
        self.assertEqual(
            record["checks"], {"CI": "success", "Unit Tests": "success"}
        )
        self.assertEqual(api.calls[-1], ("runs", "owner/example", SHA))

    def test_in_progress_workflow_is_ci_running(self):
        document, _ = self.collect(
            project(),
            runs=[
                run("CI", status="in_progress", conclusion=None),
                run("Unit Tests", run_id=2),
            ],
        )
        self.assertEqual(document["projects"][0]["status"], "ci_running")

    def test_failed_workflow_requires_fix(self):
        document, _ = self.collect(
            project(),
            runs=[run("CI", conclusion="failure"), run("Unit Tests", run_id=2)],
        )
        record = document["projects"][0]
        self.assertEqual(record["status"], "fix_required")
        self.assertIn("CI=failure", record["blocker"])

    def test_action_required_is_blocked_not_automatic_human_action(self):
        document, _ = self.collect(
            project(),
            runs=[
                run("CI", conclusion="action_required"),
                run("Unit Tests", run_id=2),
            ],
        )
        record = document["projects"][0]
        self.assertEqual(record["status"], "blocked")
        self.assertFalse(record["human_action_required"])

    def test_explicit_human_action_wins_with_blocker(self):
        document, _ = self.collect(
            project(human_action_required=True, blocker="Approve the workflow run")
        )
        record = document["projects"][0]
        self.assertEqual(record["status"], "human_action")
        self.assertTrue(record["human_action_required"])

    def test_route_unavailable_is_blocked_but_automation_owned(self):
        document, _ = self.collect(
            project(selected_auditor="none", audit_state="route-unavailable")
        )
        record = document["projects"][0]
        self.assertEqual(record["status"], "blocked")
        self.assertFalse(record["human_action_required"])
        self.assertIn("audit route", record["blocker"])

    def test_ready_to_merge_requires_non_draft_success_and_clean_audit(self):
        document, _ = self.collect(
            project(audit_state="clean"), pull_data=pull(draft=False)
        )
        record = document["projects"][0]
        self.assertEqual(record["status"], "ready_to_merge")
        self.assertIsNone(record["blocker"])

    def test_merged_pull_request_is_completed(self):
        document, _ = self.collect(
            project(audit_state="clean"),
            pull_data=pull(state="closed", merged=True, draft=False),
        )
        self.assertEqual(document["projects"][0]["status"], "completed")

    def test_closed_unmerged_pull_request_is_blocked(self):
        document, _ = self.collect(
            project(), pull_data=pull(state="closed", merged=False)
        )
        self.assertEqual(document["projects"][0]["status"], "blocked")

    def test_missing_required_workflow_is_missing_and_blocked(self):
        document, _ = self.collect(project(), runs=[run("CI")])
        record = document["projects"][0]
        self.assertEqual(record["checks"]["Unit Tests"], "missing")
        self.assertEqual(record["status"], "blocked")

    def test_latest_matching_workflow_wins_deterministically(self):
        document, _ = self.collect(
            project(),
            runs=[
                run(
                    "CI",
                    conclusion="failure",
                    updated="2026-08-04T05:50:00Z",
                    run_id=10,
                ),
                run(
                    "CI",
                    conclusion="success",
                    updated="2026-08-04T05:59:00Z",
                    run_id=11,
                ),
                run("Unit Tests", run_id=12),
            ],
        )
        self.assertEqual(document["projects"][0]["checks"]["CI"], "success")

    def test_different_head_workflow_evidence_fails_closed(self):
        with self.assertRaisesRegex(FleetCollectorError, "different head"):
            self.collect(
                project(),
                runs=[run("CI", sha=OLD_SHA), run("Unit Tests", run_id=2)],
            )

    def test_no_pr_baseline_record_makes_no_api_calls(self):
        cfg = project(
            pull_request=None,
            required_workflows=[],
            baseline_status="idle",
            selected_auditor="none",
            audit_state="required",
        )
        projects = validate_config(config(cfg))
        api = FakeApi({}, [])
        document = collect_document(projects, api, now=lambda: NOW)
        self.assertEqual(document["projects"][0]["status"], "idle")
        self.assertEqual(api.calls, [])

    def test_duplicate_repository_is_rejected(self):
        with self.assertRaisesRegex(FleetCollectorError, "duplicate repository"):
            validate_config(config(project(), project(repository="OWNER/EXAMPLE")))

    def test_duplicate_workflow_is_rejected_case_insensitively(self):
        with self.assertRaisesRegex(FleetCollectorError, "duplicate workflow"):
            validate_config(config(project(required_workflows=["CI", "ci"])))

    def test_explicit_blocker_prevents_ci_running_status(self):
        document, _ = self.collect(
            project(blocker="Repository policy blocks progress"),
            runs=[
                run("CI", status="in_progress", conclusion=None),
                run("Unit Tests", run_id=2),
            ],
        )
        self.assertEqual(document["projects"][0]["status"], "blocked")

    def test_missing_head_repository_fails_closed(self):
        bad_pull = pull()
        bad_pull["head"]["repo"] = None
        with self.assertRaisesRegex(FleetCollectorError, "head repository"):
            self.collect(project(), pull_data=bad_pull)

    def test_contradictory_merged_state_fails_closed(self):
        bad_pull = pull(state="open", merged=True, draft=False)
        with self.assertRaisesRegex(FleetCollectorError, "contradictory merged"):
            self.collect(project(audit_state="clean"), pull_data=bad_pull)

    def test_incomplete_workflow_page_fails_closed(self):
        projects = validate_config(config(project()))
        api = FakeApi(pull(), [run("CI"), run("Unit Tests", run_id=2)])
        api.runs["total_count"] = 3
        with self.assertRaisesRegex(FleetCollectorError, "incomplete exact-head"):
            collect_document(projects, api, now=lambda: NOW)

    def test_unknown_config_field_is_rejected(self):
        bad = project()
        bad["unexpected"] = True
        with self.assertRaisesRegex(FleetCollectorError, "unsupported fields"):
            validate_config(config(bad))

    def test_different_token_variables_fail_without_revealing_values(self):
        secret_a = "secret-alpha"
        secret_b = "secret-beta"
        with self.assertRaises(FleetCollectorError) as caught:
            resolve_token({"GH_TOKEN": secret_a, "GITHUB_TOKEN": secret_b})
        message = str(caught.exception)
        self.assertNotIn(secret_a, message)
        self.assertNotIn(secret_b, message)

    def test_http_error_does_not_reveal_token_or_response_body(self):
        token = "very-secret-token"
        body_secret = "private-response-secret"

        def opener(request, timeout):
            self.assertEqual(request.get_method(), "GET")
            self.assertTrue(
                request.full_url.startswith(
                    "https://api.github.com/repos/owner/example/pulls/158"
                )
            )
            raise urllib.error.HTTPError(request.full_url, 403, body_secret, {}, None)

        api = GitHubApi(token, opener=opener)
        with self.assertRaises(FleetCollectorError) as caught:
            api.get_pull("owner/example", 158)
        message = str(caught.exception)
        self.assertNotIn(token, message)
        self.assertNotIn(body_secret, message)

    def test_fixed_get_endpoints_and_authorization_header(self):
        requests = []
        responses = [pull(), {"total_count": 0, "workflow_runs": []}]

        def opener(request, timeout):
            requests.append(request)
            return FakeResponse(responses.pop(0))

        api = GitHubApi("token", opener=opener)
        api.get_pull("owner/example", 158)
        api.get_workflow_runs("owner/example", SHA)
        self.assertEqual(
            [request.get_method() for request in requests], ["GET", "GET"]
        )
        self.assertTrue(
            all(
                request.full_url.startswith(
                    "https://api.github.com/repos/owner/example/"
                )
                for request in requests
            )
        )
        self.assertTrue(
            all(
                request.headers.get("Authorization") == "Bearer token"
                for request in requests
            )
        )

    def test_output_order_is_deterministic(self):
        first = project(repository="z/repo")
        second = project(repository="a/repo")
        projects = validate_config(config(first, second))
        self.assertEqual(
            [item["repository"] for item in projects], ["a/repo", "z/repo"]
        )

    def test_check_config_performs_no_network_and_no_output_write(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "config.json"
            output_path = root / "should-not-exist.json"
            input_path.write_text(json.dumps(config(project())), encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch(
                "scripts.fleet_collect_github.GitHubApi",
                side_effect=AssertionError("network"),
            ):
                with redirect_stdout(stdout):
                    code = main([str(input_path), "--check-config"])
            self.assertEqual(code, 0)
            self.assertIn("valid: 1", stdout.getvalue())
            self.assertFalse(output_path.exists())

    def test_generated_document_is_accepted_by_renderer_contract(self):
        document, _ = self.collect(project())
        self.assertIsNotNone(validate_document(document))


if __name__ == "__main__":
    unittest.main()

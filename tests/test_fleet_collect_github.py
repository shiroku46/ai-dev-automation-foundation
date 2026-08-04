"""Tests for the fixed, read-only schema-v2 GitHub fleet collector."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.fleet_collect_github import (
    GRAPHQL_REVIEW_QUERY,
    FleetCollectorError,
    GitHubApi,
    collect_document,
    load_config,
    main,
    resolve_token,
    validate_config,
)

REPO = "example/project"
SHA = "a" * 40
NOW = datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc)


def config_project(**overrides):
    value = {
        "repository": REPO,
        "phase": "Phase 2",
        "issue": 157,
        "pull_request": 12,
        "required_workflows": ["CI", "Unit Tests"],
        "implementation_route": "github-direct",
        "risk_tier": "standard",
        "trusted_coordinators": ["trusted-owner"],
        "next_action": "Merge with expected-head protection",
        "blocker": None,
        "human_action_required": False,
        "baseline_status": None,
    }
    value.update(overrides)
    return value


def config_document(*projects, schema_version=2):
    return {"schema_version": schema_version, "projects": list(projects)}


def pull_payload(*, draft=False, state="open", merged=False, sha=SHA):
    return {
        "number": 12,
        "state": state,
        "draft": draft,
        "merged": merged,
        "merged_at": "2026-08-04T09:50:00Z" if merged else None,
        "updated_at": "2026-08-04T09:40:00Z",
        "head": {"sha": sha, "repo": {"full_name": REPO}},
        "base": {"repo": {"full_name": REPO}},
    }


def run_payload(name, *, state="success", run_id=1, sha=SHA, updated="2026-08-04T09:45:00Z"):
    if state in {"queued", "in_progress"}:
        status = state
        conclusion = None
    else:
        status = "completed"
        conclusion = state
    return {
        "id": run_id,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "head_sha": sha,
        "event": "pull_request",
        "updated_at": updated,
        "repository": {"full_name": REPO},
    }


def marker_body(marker):
    return f"{marker}\n\nReview summary: exact-head scope, checks, and correctness are clean."


def review_comment(marker, *, login="trusted-owner", edited=False):
    return {
        "body": marker_body(marker),
        "createdAt": "2026-08-04T09:47:00Z",
        "updatedAt": "2026-08-04T09:48:00Z" if edited else "2026-08-04T09:47:00Z",
        "author": {"login": login},
    }


def thread(*, resolved=True, updated="2026-08-04T09:46:00Z"):
    return {
        "isResolved": resolved,
        "comments": {"nodes": [{"updatedAt": updated}]},
    }


def graphql_payload(*, threads=None, comments=None, thread_next=False, comment_next=False, thread_cursor=None, comment_cursor=None):
    return {
        "data": {
            "repository": {
                "nameWithOwner": REPO,
                "pullRequest": {
                    "number": 12,
                    "reviewThreads": {
                        "nodes": list(threads or []),
                        "pageInfo": {
                            "hasNextPage": thread_next,
                            "endCursor": thread_cursor,
                        },
                    },
                    "comments": {
                        "nodes": list(comments or []),
                        "pageInfo": {
                            "hasNextPage": comment_next,
                            "endCursor": comment_cursor,
                        },
                    },
                },
            }
        }
    }


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def read(self, amount):
        return self.payload[:amount]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class RecordingOpener:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        if not self.payloads:
            raise AssertionError("unexpected network request")
        payload = self.payloads.pop(0)
        if isinstance(payload, BaseException):
            raise payload
        return FakeResponse(payload)


class NoNetworkApi:
    def get_pull(self, repository, number):
        raise AssertionError("baseline collection must not call GitHub")

    def get_workflow_runs(self, repository, sha):
        raise AssertionError("baseline collection must not call GitHub")

    def get_review_evidence(self, repository, number):
        raise AssertionError("baseline collection must not call GitHub")


def api_for_standard_clean(*, draft=False, threads=None, comments=None, route_runs=None, token="token"):
    comments = comments or [
        review_comment(f"<!-- foundation-coordinator-review:{SHA}:clean -->")
    ]
    runs = route_runs or [run_payload("CI", run_id=1), run_payload("Unit Tests", run_id=2)]
    opener = RecordingOpener(
        [
            pull_payload(draft=draft),
            {"total_count": len(runs), "workflow_runs": runs},
            graphql_payload(threads=threads or [], comments=comments),
        ]
    )
    return GitHubApi(token, opener), opener


class ConfigValidationTest(unittest.TestCase):
    def test_valid_schema_v2_config(self):
        projects = validate_config(config_document(config_project()))
        self.assertEqual(projects[0]["trusted_coordinators"], ("trusted-owner",))

    def test_legacy_schema_and_auditor_fields_fail_closed(self):
        with self.assertRaisesRegex(FleetCollectorError, "legacy provider-auditor"):
            validate_config(config_document(config_project(), schema_version=1))
        value = config_project()
        value["selected_auditor"] = "codex"
        value["audit_state"] = "clean"
        with self.assertRaisesRegex(FleetCollectorError, "unsupported fields"):
            validate_config(config_document(value))

    def test_optional_provider_routes_are_valid_metadata(self):
        for route in ("codex-optional", "claude-optional"):
            with self.subTest(route=route):
                validate_config(config_document(config_project(implementation_route=route)))

    def test_duplicate_coordinator_and_repository_are_rejected(self):
        with self.assertRaisesRegex(FleetCollectorError, "duplicate login"):
            validate_config(
                config_document(
                    config_project(trusted_coordinators=["Owner", "owner"])
                )
            )
        with self.assertRaisesRegex(FleetCollectorError, "duplicate repository"):
            validate_config(
                config_document(config_project(), config_project(repository="Example/Project"))
            )

    def test_non_pr_configuration_is_strict(self):
        valid = config_project(
            pull_request=None,
            required_workflows=[],
            baseline_status="idle",
        )
        validate_config(config_document(valid))
        invalid = dict(valid)
        invalid["required_workflows"] = ["CI"]
        with self.assertRaisesRegex(FleetCollectorError, "contradictory non-PR"):
            validate_config(config_document(invalid))

    def test_human_action_requires_blocker(self):
        with self.assertRaisesRegex(FleetCollectorError, "blocker is required"):
            validate_config(
                config_document(config_project(human_action_required=True))
            )

    def test_load_config_rejects_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                '{"schema_version":2,"schema_version":2,"projects":[]}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(FleetCollectorError, "duplicate object key"):
                load_config(path)


class TokenAndEndpointTest(unittest.TestCase):
    def test_token_resolution_rejects_conflicts_and_newlines(self):
        with self.assertRaisesRegex(FleetCollectorError, "different values"):
            resolve_token({"GH_TOKEN": "one", "GITHUB_TOKEN": "two"})
        with self.assertRaisesRegex(FleetCollectorError, "invalid"):
            resolve_token({"GH_TOKEN": "secret\nvalue"})
        self.assertEqual(resolve_token({"GH_TOKEN": "same", "GITHUB_TOKEN": "same"}), "same")

    def test_rest_endpoint_and_query_are_fixed(self):
        api = GitHubApi(None, RecordingOpener([]))
        with self.assertRaisesRegex(FleetCollectorError, "fixed GitHub REST families"):
            api._rest_get("/repos/example/project/issues/1")
        with self.assertRaisesRegex(FleetCollectorError, "fixed contract"):
            api._rest_get(
                "/repos/example/project/actions/runs",
                {"event": "push", "head_sha": SHA, "per_page": "100", "page": "1"},
            )

    def test_graphql_requires_token_and_uses_static_query(self):
        with self.assertRaisesRegex(FleetCollectorError, "token is required"):
            GitHubApi(None, RecordingOpener([])).get_review_evidence(REPO, 12)
        opener = RecordingOpener([graphql_payload()])
        api = GitHubApi("token", opener)
        api.get_review_evidence(REPO, 12)
        request, timeout = opener.requests[0]
        self.assertEqual(request.full_url, "https://api.github.com/graphql")
        self.assertEqual(request.get_method(), "POST")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["query"], GRAPHQL_REVIEW_QUERY)
        self.assertNotIn("mutation", body["query"].lower())
        self.assertEqual(body["variables"]["owner"], "example")
        self.assertEqual(timeout, 15)

    def test_http_error_never_echoes_response_or_token(self):
        error = urllib.error.HTTPError(
            "https://api.github.com/graphql",
            403,
            "forbidden secret-body",
            {},
            None,
        )
        api = GitHubApi("super-secret-token", RecordingOpener([error]))
        with self.assertRaises(FleetCollectorError) as captured:
            api.get_review_evidence(REPO, 12)
        message = str(captured.exception)
        self.assertNotIn("super-secret-token", message)
        self.assertNotIn("secret-body", message)


class CollectionTest(unittest.TestCase):
    def test_standard_clean_record_is_ready_to_merge(self):
        api, _ = api_for_standard_clean()
        payload = collect_document(
            validate_config(config_document(config_project())),
            api,
            now=lambda: NOW,
        )
        record = payload["projects"][0]
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(record["status"], "ready_to_merge")
        self.assertEqual(record["review_route"], "github-coordinator")
        self.assertEqual(record["review_state"], "clean")
        self.assertEqual(record["unresolved_review_threads"], 0)
        self.assertNotIn("selected_auditor", record)
        self.assertNotIn("audit_state", record)

    def test_draft_with_clean_review_remains_pr_open(self):
        api, _ = api_for_standard_clean(draft=True)
        record = collect_document(
            validate_config(config_document(config_project())), api, now=lambda: NOW
        )["projects"][0]
        self.assertEqual(record["status"], "pr_open")
        self.assertEqual(record["review_state"], "clean")

    def test_protected_requires_both_clean_passes(self):
        project = config_project(risk_tier="protected")
        comments = [
            review_comment(
                f"<!-- foundation-coordinator-review:{SHA}:scope-security:clean -->"
            ),
            review_comment(
                f"<!-- foundation-coordinator-review:{SHA}:correctness-race:clean -->"
            ),
        ]
        api, _ = api_for_standard_clean(comments=comments)
        record = collect_document(
            validate_config(config_document(project)), api, now=lambda: NOW
        )["projects"][0]
        self.assertEqual(record["review_state"], "clean")
        self.assertEqual(record["status"], "ready_to_merge")

    def test_protected_single_pass_is_pending(self):
        project = config_project(risk_tier="protected")
        comments = [
            review_comment(
                f"<!-- foundation-coordinator-review:{SHA}:scope-security:clean -->"
            )
        ]
        api, _ = api_for_standard_clean(comments=comments)
        record = collect_document(
            validate_config(config_document(project)), api, now=lambda: NOW
        )["projects"][0]
        self.assertEqual(record["review_state"], "pending")
        self.assertEqual(record["status"], "review_required")

    def test_unresolved_thread_prevents_clean_state(self):
        api, _ = api_for_standard_clean(threads=[thread(resolved=False)])
        record = collect_document(
            validate_config(config_document(config_project())), api, now=lambda: NOW
        )["projects"][0]
        self.assertEqual(record["unresolved_review_threads"], 1)
        self.assertEqual(record["review_state"], "pending")
        self.assertEqual(record["status"], "review_required")

    def test_blocked_marker_creates_automation_owned_blocker(self):
        comments = [
            review_comment(f"<!-- foundation-coordinator-review:{SHA}:blocked -->")
        ]
        api, _ = api_for_standard_clean(comments=comments)
        record = collect_document(
            validate_config(config_document(config_project())), api, now=lambda: NOW
        )["projects"][0]
        self.assertEqual(record["review_state"], "blocked")
        self.assertEqual(record["status"], "blocked")
        self.assertFalse(record["human_action_required"])
        self.assertIn("blocking finding", record["blocker"])

    def test_edited_untrusted_stale_and_duplicate_markers_do_not_clean(self):
        cases = [
            [review_comment(f"<!-- foundation-coordinator-review:{SHA}:clean -->", edited=True)],
            [review_comment(f"<!-- foundation-coordinator-review:{SHA}:clean -->", login="outsider")],
            [review_comment(f"<!-- foundation-coordinator-review:{'b' * 40}:clean -->")],
            [
                review_comment(f"<!-- foundation-coordinator-review:{SHA}:clean -->"),
                review_comment(f"<!-- foundation-coordinator-review:{SHA}:clean -->"),
            ],
        ]
        for comments in cases:
            with self.subTest(comments=comments):
                api, _ = api_for_standard_clean(comments=comments)
                record = collect_document(
                    validate_config(config_document(config_project())),
                    api,
                    now=lambda: NOW,
                )["projects"][0]
                self.assertNotEqual(record["review_state"], "clean")
                self.assertEqual(record["status"], "review_required")

    def test_optional_provider_route_does_not_affect_status(self):
        for route in ("codex-optional", "claude-optional"):
            with self.subTest(route=route):
                api, _ = api_for_standard_clean()
                record = collect_document(
                    validate_config(
                        config_document(config_project(implementation_route=route))
                    ),
                    api,
                    now=lambda: NOW,
                )["projects"][0]
                self.assertEqual(record["status"], "ready_to_merge")
                self.assertFalse(record["human_action_required"])

    def test_failed_missing_and_pending_checks_are_conservative(self):
        cases = [
            ("failure", "fix_required"),
            ("action_required", "blocked"),
            ("in_progress", "ci_running"),
        ]
        for state, expected in cases:
            with self.subTest(state=state):
                runs = [
                    run_payload("CI", state=state, run_id=1),
                    run_payload("Unit Tests", run_id=2),
                ]
                api, _ = api_for_standard_clean(route_runs=runs)
                record = collect_document(
                    validate_config(config_document(config_project())),
                    api,
                    now=lambda: NOW,
                )["projects"][0]
                self.assertEqual(record["status"], expected)

    def test_workflow_head_mismatch_fails_closed(self):
        runs = [
            run_payload("CI", run_id=1, sha="b" * 40),
            run_payload("Unit Tests", run_id=2),
        ]
        api, _ = api_for_standard_clean(route_runs=runs)
        with self.assertRaisesRegex(FleetCollectorError, "different identity"):
            collect_document(
                validate_config(config_document(config_project())),
                api,
                now=lambda: NOW,
            )

    def test_workflow_pagination_is_complete(self):
        first_page = [run_payload("Other", run_id=index + 1) for index in range(100)]
        second_page = [run_payload("CI", run_id=101), run_payload("Unit Tests", run_id=102)]
        opener = RecordingOpener(
            [
                pull_payload(),
                {"total_count": 102, "workflow_runs": first_page},
                {"total_count": 102, "workflow_runs": second_page},
                graphql_payload(
                    comments=[
                        review_comment(
                            f"<!-- foundation-coordinator-review:{SHA}:clean -->"
                        )
                    ]
                ),
            ]
        )
        record = collect_document(
            validate_config(config_document(config_project())),
            GitHubApi("token", opener),
            now=lambda: NOW,
        )["projects"][0]
        self.assertEqual(record["status"], "ready_to_merge")
        self.assertEqual(len(opener.requests), 4)

    def test_incomplete_workflow_pagination_fails_closed(self):
        first_page = [run_payload("Other", run_id=index + 1) for index in range(100)]
        opener = RecordingOpener(
            [
                pull_payload(),
                {"total_count": 101, "workflow_runs": first_page},
                {"total_count": 101, "workflow_runs": []},
            ]
        )
        with self.assertRaisesRegex(FleetCollectorError, "pagination was incomplete"):
            collect_document(
                validate_config(config_document(config_project())),
                GitHubApi("token", opener),
                now=lambda: NOW,
            )

    def test_graphql_review_thread_pagination_is_complete(self):
        opener = RecordingOpener(
            [
                pull_payload(),
                {
                    "total_count": 2,
                    "workflow_runs": [
                        run_payload("CI", run_id=1),
                        run_payload("Unit Tests", run_id=2),
                    ],
                },
                graphql_payload(
                    threads=[thread(resolved=True)],
                    comments=[
                        review_comment(
                            f"<!-- foundation-coordinator-review:{SHA}:clean -->"
                        )
                    ],
                    thread_next=True,
                    thread_cursor="thread-page-2",
                ),
                graphql_payload(
                    threads=[thread(resolved=True, updated="2026-08-04T09:49:00Z")],
                    comments=[],
                ),
            ]
        )
        record = collect_document(
            validate_config(config_document(config_project())),
            GitHubApi("token", opener),
            now=lambda: NOW,
        )["projects"][0]
        self.assertEqual(record["review_state"], "clean")
        self.assertEqual(record["unresolved_review_threads"], 0)

    def test_baseline_record_performs_no_network(self):
        project = config_project(
            pull_request=None,
            required_workflows=[],
            baseline_status="idle",
        )
        record = collect_document(
            validate_config(config_document(project)),
            NoNetworkApi(),
            now=lambda: NOW,
        )["projects"][0]
        self.assertEqual(record["status"], "idle")
        self.assertEqual(record["review_state"], "required")
        self.assertIsNone(record["head_sha"])


class CommandTest(unittest.TestCase):
    def test_check_config_is_offline_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "config.json"
            path.write_text(json.dumps(config_document(config_project())), encoding="utf-8")
            stdout = io.StringIO()
            with patch("scripts.fleet_collect_github.GitHubApi") as api_class, contextlib.redirect_stdout(stdout):
                result = main([str(path), "--check-config"])
            self.assertEqual(result, 0)
            api_class.assert_not_called()
            self.assertEqual(sorted(item.name for item in root.iterdir()), ["config.json"])
            self.assertIn("valid: 1 project configurations", stdout.getvalue())

    def test_check_config_and_output_are_mutually_exclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config_document(config_project())), encoding="utf-8")
            with self.assertRaises(SystemExit):
                main([str(path), "--check-config", "--output", str(path.with_suffix(".out"))])

    def test_pr_collection_without_token_fails_safely(self):
        api, opener = api_for_standard_clean(token=None)
        with self.assertRaisesRegex(FleetCollectorError, "token is required"):
            collect_document(
                validate_config(config_document(config_project())),
                api,
                now=lambda: NOW,
            )
        self.assertEqual(len(opener.requests), 2)


if __name__ == "__main__":
    unittest.main()

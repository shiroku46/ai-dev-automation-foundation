#!/usr/bin/env python3
"""Collect schema-version-2 Fleet Progress records from fixed GitHub evidence.

The collector is read-only. It uses fixed REST GET endpoint families and one
static GraphQL query for authoritative review-thread resolution and trusted
coordinator-review comments. Configuration cannot select a host, endpoint,
method, header, query document, workflow dispatch, or mutation.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

try:
    from scripts.fleet_progress import (
        CHECK_NAME_RE,
        CHECK_STATES,
        IMPLEMENTATION_ROUTES,
        PASSING_CHECK_STATES,
        REPOSITORY_RE,
        RISK_TIERS,
        FleetProgressError,
        validate_document,
    )
except ImportError:
    from fleet_progress import (  # type: ignore[no-redef]
        CHECK_NAME_RE,
        CHECK_STATES,
        IMPLEMENTATION_ROUTES,
        PASSING_CHECK_STATES,
        REPOSITORY_RE,
        RISK_TIERS,
        FleetProgressError,
        validate_document,
    )

API_BASE = "https://api.github.com"
GRAPHQL_URL = "https://api.github.com/graphql"
MAX_CONFIG_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_PROJECTS = 50
MAX_WORKFLOWS = 16
MAX_COORDINATORS = 10
MAX_RUN_PAGES = 10
MAX_REVIEW_PAGES = 10
MAX_RUNS = 1000
MAX_REVIEW_THREADS = 1000
MAX_REVIEW_COMMENTS = 1000
TIMEOUT_SECONDS = 15

BASELINES = frozenset({"backlog", "ready", "idle"})
BAD_CHECKS = frozenset({"failure", "cancelled", "timed_out", "stale"})
PENDING_CHECKS = frozenset({"queued", "in_progress"})
BLOCKED_CHECKS = frozenset({"missing", "action_required"})
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
NODE_ID_RE = re.compile(r"^[A-Za-z0-9_:\-=]{1,256}$")
PULL_PATH_RE = re.compile(
    r"^/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pulls/[1-9][0-9]*$"
)
RUNS_PATH_RE = re.compile(
    r"^/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/actions/runs$"
)
GENERAL_MARKER_RE = re.compile(
    r"<!-- foundation-coordinator-review:([0-9a-f]{40}):(clean|blocked) -->"
)
PASS_MARKER_RE = re.compile(
    r"<!-- foundation-coordinator-review:([0-9a-f]{40}):"
    r"(scope-security|correctness-race):(clean|blocked) -->"
)
PROJECT_KEYS = frozenset(
    {
        "repository",
        "phase",
        "issue",
        "pull_request",
        "required_workflows",
        "implementation_route",
        "risk_tier",
        "trusted_coordinators",
        "next_action",
        "blocker",
        "human_action_required",
        "baseline_status",
    }
)

GRAPHQL_REVIEW_QUERY = """
query FleetReviewEvidence(
  $owner: String!
  $name: String!
  $number: Int!
  $threadsCursor: String
  $commentsCursor: String
) {
  repository(owner: $owner, name: $name) {
    nameWithOwner
    pullRequest(number: $number) {
      number
      headRefOid
      state
      isDraft
      merged
      updatedAt
      headRepository { nameWithOwner }
      baseRepository { nameWithOwner }
      reviewThreads(first: 100, after: $threadsCursor) {
        nodes {
          id
          isResolved
          comments(last: 1) {
            nodes { updatedAt }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
      comments(first: 100, after: $commentsCursor) {
        nodes {
          id
          body
          createdAt
          updatedAt
          author { login }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
""".strip()

if re.search(r"\bmutation\b", GRAPHQL_REVIEW_QUERY, re.IGNORECASE):
    raise RuntimeError("Fleet review GraphQL source must never contain a mutation")


class FleetCollectorError(ValueError):
    """Raised when configuration or connected evidence fails closed."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FleetCollectorError(f"JSON contains duplicate object key: {key}")
        result[key] = value
    return result


def _json_loads(raw: str | bytes, where: str) -> Any:
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except FleetCollectorError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FleetCollectorError(f"{where} returned invalid JSON") from exc


def _obj(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise FleetCollectorError(f"{where} must be an object")
    return value


def _keys(value: Mapping[str, Any], expected: frozenset[str], where: str) -> None:
    missing = sorted(expected - value.keys())
    extra = sorted(value.keys() - expected)
    if missing:
        raise FleetCollectorError(f"{where} is missing fields: {', '.join(missing)}")
    if extra:
        raise FleetCollectorError(f"{where} has unsupported fields: {', '.join(extra)}")


def _text(value: Any, where: str, limit: int = 500) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FleetCollectorError(f"{where} must be a non-empty string")
    text = value.strip()
    if len(text) > limit or CONTROL_RE.search(text):
        raise FleetCollectorError(f"{where} is not bounded safe text")
    return text


def _enum(value: Any, allowed: frozenset[str], where: str) -> str:
    text = _text(value, where, 80)
    if text not in allowed:
        raise FleetCollectorError(f"{where} has an unsupported value")
    return text


def _number(value: Any, where: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise FleetCollectorError(f"{where} must be a positive integer or null")
    return value


def _timestamp(value: Any, where: str) -> str:
    text = _text(value, where, 40)
    if not UTC_RE.fullmatch(text):
        raise FleetCollectorError(f"{where} must be an ISO-8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise FleetCollectorError(f"{where} must be a valid UTC timestamp") from exc
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value[:-1] + "+00:00")


def _now(clock: Callable[[], datetime]) -> str:
    return clock().astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _max_timestamp(values: Sequence[str], fallback: str) -> str:
    return max(values, key=_dt) if values else fallback


def _node_id(value: Any, where: str) -> str:
    if not isinstance(value, str) or not NODE_ID_RE.fullmatch(value):
        raise FleetCollectorError(f"{where} is invalid")
    return value


def validate_config(value: Any) -> tuple[dict[str, Any], ...]:
    document = _obj(value, "document")
    _keys(document, frozenset({"schema_version", "projects"}), "document")
    if isinstance(document["schema_version"], bool) or document["schema_version"] != 2:
        raise FleetCollectorError(
            "document.schema_version must equal 2; legacy provider-auditor configuration is unsupported"
        )
    raw_projects = document["projects"]
    if not isinstance(raw_projects, list) or len(raw_projects) > MAX_PROJECTS:
        raise FleetCollectorError(
            f"document.projects must contain at most {MAX_PROJECTS} items"
        )

    projects: list[dict[str, Any]] = []
    seen_repositories: set[str] = set()
    for index, item in enumerate(raw_projects):
        where = f"projects[{index}]"
        project = _obj(item, where)
        _keys(project, PROJECT_KEYS, where)

        repository = _text(project["repository"], f"{where}.repository", 200)
        if not REPOSITORY_RE.fullmatch(repository):
            raise FleetCollectorError(f"{where}.repository must use owner/name form")
        repository_key = repository.casefold()
        if repository_key in seen_repositories:
            raise FleetCollectorError(
                f"document.projects contains duplicate repository {repository}"
            )
        seen_repositories.add(repository_key)

        workflows = project["required_workflows"]
        if not isinstance(workflows, list) or len(workflows) > MAX_WORKFLOWS:
            raise FleetCollectorError(f"{where}.required_workflows is invalid")
        normalized_workflows: list[str] = []
        workflow_keys: set[str] = set()
        for workflow_index, workflow in enumerate(workflows):
            name = _text(
                workflow,
                f"{where}.required_workflows[{workflow_index}]",
                120,
            )
            key = name.casefold()
            if not CHECK_NAME_RE.fullmatch(name) or key in workflow_keys:
                raise FleetCollectorError(
                    f"{where}.required_workflows contains an invalid or duplicate workflow"
                )
            workflow_keys.add(key)
            normalized_workflows.append(name)

        coordinators = project["trusted_coordinators"]
        if (
            not isinstance(coordinators, list)
            or not coordinators
            or len(coordinators) > MAX_COORDINATORS
        ):
            raise FleetCollectorError(f"{where}.trusted_coordinators is invalid")
        normalized_coordinators: list[str] = []
        coordinator_keys: set[str] = set()
        for coordinator_index, coordinator in enumerate(coordinators):
            login = _text(
                coordinator,
                f"{where}.trusted_coordinators[{coordinator_index}]",
                39,
            )
            key = login.casefold()
            if not LOGIN_RE.fullmatch(login) or key in coordinator_keys:
                raise FleetCollectorError(
                    f"{where}.trusted_coordinators contains an invalid or duplicate login"
                )
            coordinator_keys.add(key)
            normalized_coordinators.append(login)

        pull_request = _number(project["pull_request"], f"{where}.pull_request")
        blocker = (
            None
            if project["blocker"] is None
            else _text(project["blocker"], f"{where}.blocker")
        )
        human_action = project["human_action_required"]
        if not isinstance(human_action, bool):
            raise FleetCollectorError(
                f"{where}.human_action_required must be a boolean"
            )
        if human_action and blocker is None:
            raise FleetCollectorError(f"{where}.blocker is required for human action")

        baseline = (
            None
            if project["baseline_status"] is None
            else _enum(
                project["baseline_status"], BASELINES, f"{where}.baseline_status"
            )
        )
        if pull_request is None:
            if normalized_workflows or baseline is None or human_action or blocker is not None:
                raise FleetCollectorError(f"{where} has contradictory non-PR state")
        elif not normalized_workflows or baseline is not None:
            raise FleetCollectorError(f"{where} has contradictory Pull Request state")

        projects.append(
            {
                "repository": repository,
                "phase": _text(project["phase"], f"{where}.phase", 160),
                "issue": _number(project["issue"], f"{where}.issue"),
                "pull_request": pull_request,
                "required_workflows": tuple(
                    sorted(normalized_workflows, key=str.casefold)
                ),
                "implementation_route": _enum(
                    project["implementation_route"],
                    IMPLEMENTATION_ROUTES,
                    f"{where}.implementation_route",
                ),
                "risk_tier": _enum(
                    project["risk_tier"], RISK_TIERS, f"{where}.risk_tier"
                ),
                "trusted_coordinators": tuple(
                    sorted(normalized_coordinators, key=str.casefold)
                ),
                "next_action": _text(
                    project["next_action"], f"{where}.next_action"
                ),
                "blocker": blocker,
                "human_action_required": human_action,
                "baseline_status": baseline,
            }
        )
    return tuple(sorted(projects, key=lambda item: item["repository"].casefold()))


def load_config(path: Path) -> tuple[dict[str, Any], ...]:
    try:
        if path.stat().st_size > MAX_CONFIG_BYTES:
            raise FleetCollectorError("input file exceeded the bounded size limit")
        raw = path.read_text(encoding="utf-8")
    except FleetCollectorError:
        raise
    except (OSError, UnicodeError) as exc:
        detail = getattr(exc, "strerror", None) or "invalid UTF-8 or I/O error"
        raise FleetCollectorError(f"cannot read input file: {detail}") from exc
    return validate_config(_json_loads(raw, "input"))


def resolve_token(environment: Mapping[str, str]) -> str | None:
    first = environment.get("GH_TOKEN") or None
    second = environment.get("GITHUB_TOKEN") or None
    if first is not None and second is not None and first != second:
        raise FleetCollectorError(
            "GH_TOKEN and GITHUB_TOKEN are both set with different values"
        )
    token = first or second
    if token is not None and (
        not token or len(token) > 4096 or "\r" in token or "\n" in token
    ):
        raise FleetCollectorError("GitHub token environment value is invalid")
    return token


class GitHubApi:
    """Fixed-host read-only GitHub client with bounded response handling."""

    def __init__(
        self,
        token: str | None,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.token = token
        self.opener = opener or urllib.request.urlopen

    def _read_json(self, request: urllib.request.Request) -> Mapping[str, Any]:
        try:
            with self.opener(request, timeout=TIMEOUT_SECONDS) as response:
                payload = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                detail = "authentication was rejected"
            elif exc.code == 403:
                detail = (
                    "rate limit is exhausted"
                    if exc.headers
                    and exc.headers.get("X-RateLimit-Remaining") == "0"
                    else "access was forbidden"
                )
            elif exc.code == 404:
                detail = "resource was not found or is not accessible"
            else:
                detail = f"request failed with HTTP {exc.code}"
            raise FleetCollectorError(f"GitHub API {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise FleetCollectorError("GitHub API network request failed") from exc
        if len(payload) > MAX_RESPONSE_BYTES:
            raise FleetCollectorError(
                "GitHub API response exceeded the bounded size limit"
            )
        return _obj(_json_loads(payload, "GitHub API"), "GitHub API response")

    def _rest_get(
        self,
        path: str,
        query: Mapping[str, str] | None = None,
    ) -> Mapping[str, Any]:
        if PULL_PATH_RE.fullmatch(path):
            if query is not None:
                raise FleetCollectorError(
                    "Pull Request endpoint does not accept query input"
                )
        elif RUNS_PATH_RE.fullmatch(path):
            if query is None or set(query) != {
                "event",
                "head_sha",
                "per_page",
                "page",
            }:
                raise FleetCollectorError(
                    "workflow endpoint query escaped the fixed contract"
                )
            try:
                page = int(query["page"])
            except (KeyError, TypeError, ValueError) as exc:
                raise FleetCollectorError("workflow page is invalid") from exc
            if (
                query["event"] != "pull_request"
                or query["per_page"] != "100"
                or not SHA_RE.fullmatch(query["head_sha"])
                or page < 1
                or page > MAX_RUN_PAGES
            ):
                raise FleetCollectorError(
                    "workflow endpoint query escaped the fixed contract"
                )
        else:
            raise FleetCollectorError(
                "internal endpoint escaped the fixed GitHub REST families"
            )

        url = API_BASE + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        parsed = urllib.parse.urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "api.github.com"
            or parsed.port is not None
        ):
            raise FleetCollectorError(
                "internal endpoint escaped the fixed GitHub API host"
            )
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "foundation-fleet-collector/2",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return self._read_json(
            urllib.request.Request(url, headers=headers, method="GET")
        )

    def _graphql(
        self,
        owner: str,
        name: str,
        number: int,
        threads_cursor: str | None,
        comments_cursor: str | None,
    ) -> Mapping[str, Any]:
        if not self.token:
            raise FleetCollectorError(
                "GitHub token is required for authoritative review evidence"
            )
        if (
            not owner
            or not name
            or isinstance(number, bool)
            or not isinstance(number, int)
            or number <= 0
        ):
            raise FleetCollectorError("invalid GraphQL Pull Request identity")
        for cursor in (threads_cursor, comments_cursor):
            if cursor is not None and (
                not isinstance(cursor, str)
                or not cursor
                or len(cursor) > 512
                or CONTROL_RE.search(cursor)
            ):
                raise FleetCollectorError("GraphQL pagination cursor is invalid")
        body = json.dumps(
            {
                "query": GRAPHQL_REVIEW_QUERY,
                "variables": {
                    "owner": owner,
                    "name": name,
                    "number": number,
                    "threadsCursor": threads_cursor,
                    "commentsCursor": comments_cursor,
                },
            },
            separators=(",", ":"),
        ).encode("utf-8")
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "foundation-fleet-collector/2",
        }
        request = urllib.request.Request(
            GRAPHQL_URL,
            data=body,
            headers=headers,
            method="POST",
        )
        if request.full_url != GRAPHQL_URL or request.get_method() != "POST":
            raise FleetCollectorError("GraphQL request escaped the fixed contract")
        return self._read_json(request)

    def get_pull(self, repository: str, number: int) -> Mapping[str, Any]:
        if (
            not REPOSITORY_RE.fullmatch(repository)
            or isinstance(number, bool)
            or not isinstance(number, int)
            or number <= 0
        ):
            raise FleetCollectorError("invalid Pull Request endpoint identity")
        owner, name = repository.split("/", 1)
        return self._rest_get(
            f"/repos/{urllib.parse.quote(owner, safe='')}/"
            f"{urllib.parse.quote(name, safe='')}/pulls/{number}"
        )

    def get_workflow_runs(self, repository: str, sha: str) -> Mapping[str, Any]:
        if not REPOSITORY_RE.fullmatch(repository) or not SHA_RE.fullmatch(sha):
            raise FleetCollectorError("invalid workflow endpoint identity")
        owner, name = repository.split("/", 1)
        path = (
            f"/repos/{urllib.parse.quote(owner, safe='')}/"
            f"{urllib.parse.quote(name, safe='')}/actions/runs"
        )
        all_runs: list[Any] = []
        seen_ids: set[int] = set()
        expected_total: int | None = None
        for page in range(1, MAX_RUN_PAGES + 1):
            response = self._rest_get(
                path,
                {
                    "event": "pull_request",
                    "head_sha": sha,
                    "per_page": "100",
                    "page": str(page),
                },
            )
            runs = response.get("workflow_runs")
            total = response.get("total_count")
            if (
                not isinstance(runs, list)
                or isinstance(total, bool)
                or not isinstance(total, int)
                or total < 0
                or total > MAX_RUNS
            ):
                raise FleetCollectorError(
                    "GitHub returned incomplete exact-head workflow evidence"
                )
            if expected_total is None:
                expected_total = total
            elif total != expected_total:
                raise FleetCollectorError(
                    "GitHub workflow total changed during pagination"
                )
            for value in runs:
                run = _obj(value, "workflow run")
                run_id = run.get("id")
                if (
                    isinstance(run_id, bool)
                    or not isinstance(run_id, int)
                    or run_id <= 0
                    or run_id in seen_ids
                ):
                    raise FleetCollectorError(
                        "GitHub returned duplicate or invalid workflow evidence"
                    )
                seen_ids.add(run_id)
                all_runs.append(run)
            if len(all_runs) >= total:
                break
            if not runs:
                break
        if expected_total is None or len(all_runs) != expected_total:
            raise FleetCollectorError(
                "GitHub exact-head workflow pagination was incomplete"
            )
        return {"total_count": expected_total, "workflow_runs": all_runs}

    def get_review_evidence(
        self,
        repository: str,
        number: int,
        expected_sha: str,
    ) -> Mapping[str, Any]:
        if (
            not REPOSITORY_RE.fullmatch(repository)
            or not SHA_RE.fullmatch(expected_sha)
        ):
            raise FleetCollectorError("invalid review-evidence identity")
        owner, name = repository.split("/", 1)
        threads_cursor: str | None = None
        comments_cursor: str | None = None
        threads_done = False
        comments_done = False
        thread_nodes: list[Mapping[str, Any]] = []
        comment_nodes: list[Mapping[str, Any]] = []
        seen_thread_cursors: set[str] = set()
        seen_comment_cursors: set[str] = set()
        seen_thread_ids: set[str] = set()
        seen_comment_ids: set[str] = set()
        expected_snapshot: Mapping[str, Any] | None = None

        for _ in range(MAX_REVIEW_PAGES):
            response = self._graphql(
                owner,
                name,
                number,
                threads_cursor,
                comments_cursor,
            )
            errors = response.get("errors")
            if errors not in (None, []):
                raise FleetCollectorError("GitHub GraphQL review query failed")
            data = _obj(response.get("data"), "GraphQL data")
            repository_data = _obj(data.get("repository"), "GraphQL repository")
            if str(repository_data.get("nameWithOwner") or "").casefold() != repository.casefold():
                raise FleetCollectorError("GraphQL repository identity mismatch")
            pull = _obj(
                repository_data.get("pullRequest"),
                "GraphQL Pull Request",
            )
            snapshot = _graphql_pull_snapshot(
                pull,
                repository,
                number,
                expected_sha,
            )
            if expected_snapshot is None:
                expected_snapshot = snapshot
            elif snapshot != expected_snapshot:
                raise FleetCollectorError(
                    "GraphQL Pull Request identity changed during pagination"
                )

            if not threads_done:
                connection = _obj(
                    pull.get("reviewThreads"),
                    "GraphQL reviewThreads",
                )
                nodes = connection.get("nodes")
                page_info = _obj(
                    connection.get("pageInfo"),
                    "GraphQL reviewThreads pageInfo",
                )
                if not isinstance(nodes, list) or len(nodes) > 100:
                    raise FleetCollectorError("GraphQL reviewThreads page is invalid")
                for node in nodes:
                    item = _obj(node, "GraphQL review thread")
                    node_id = _node_id(item.get("id"), "review thread id")
                    if node_id in seen_thread_ids:
                        raise FleetCollectorError(
                            "GraphQL review thread pagination contained a duplicate node"
                        )
                    seen_thread_ids.add(node_id)
                    thread_nodes.append(item)
                if len(thread_nodes) > MAX_REVIEW_THREADS:
                    raise FleetCollectorError("review-thread evidence exceeded its bound")
                threads_done, threads_cursor = _advance_page(
                    page_info,
                    threads_cursor,
                    seen_thread_cursors,
                    "reviewThreads",
                )

            if not comments_done:
                connection = _obj(
                    pull.get("comments"),
                    "GraphQL comments",
                )
                nodes = connection.get("nodes")
                page_info = _obj(
                    connection.get("pageInfo"),
                    "GraphQL comments pageInfo",
                )
                if not isinstance(nodes, list) or len(nodes) > 100:
                    raise FleetCollectorError("GraphQL comments page is invalid")
                for node in nodes:
                    item = _obj(node, "GraphQL coordinator comment")
                    node_id = _node_id(item.get("id"), "coordinator comment id")
                    if node_id in seen_comment_ids:
                        raise FleetCollectorError(
                            "GraphQL comment pagination contained a duplicate node"
                        )
                    seen_comment_ids.add(node_id)
                    comment_nodes.append(item)
                if len(comment_nodes) > MAX_REVIEW_COMMENTS:
                    raise FleetCollectorError("review-comment evidence exceeded its bound")
                comments_done, comments_cursor = _advance_page(
                    page_info,
                    comments_cursor,
                    seen_comment_cursors,
                    "comments",
                )

            if threads_done and comments_done:
                if expected_snapshot is None:
                    raise FleetCollectorError("GraphQL Pull Request snapshot is absent")
                return {
                    "snapshot": expected_snapshot,
                    "threads": thread_nodes,
                    "comments": comment_nodes,
                }
        raise FleetCollectorError("GraphQL review evidence pagination exceeded its bound")


def _advance_page(
    page_info: Mapping[str, Any],
    current_cursor: str | None,
    seen: set[str],
    where: str,
) -> tuple[bool, str | None]:
    has_next = page_info.get("hasNextPage")
    end_cursor = page_info.get("endCursor")
    if not isinstance(has_next, bool):
        raise FleetCollectorError(f"GraphQL {where} pageInfo is invalid")
    if not has_next:
        if end_cursor is not None and not isinstance(end_cursor, str):
            raise FleetCollectorError(f"GraphQL {where} endCursor is invalid")
        return True, current_cursor
    if (
        not isinstance(end_cursor, str)
        or not end_cursor
        or len(end_cursor) > 512
        or CONTROL_RE.search(end_cursor)
        or end_cursor == current_cursor
        or end_cursor in seen
    ):
        raise FleetCollectorError(f"GraphQL {where} pagination cursor is invalid")
    seen.add(end_cursor)
    return False, end_cursor


def _normalize_state(state: Any, merged: Any, where: str) -> tuple[str, bool]:
    if not isinstance(merged, bool):
        raise FleetCollectorError(f"{where} merged evidence is invalid")
    if state == "OPEN" and not merged:
        return "open", False
    if state == "CLOSED" and not merged:
        return "closed", False
    if state == "MERGED" and merged:
        return "closed", True
    raise FleetCollectorError(f"{where} state and merged evidence conflict")


def _graphql_pull_snapshot(
    pull: Mapping[str, Any],
    repository: str,
    number: int,
    expected_sha: str,
) -> dict[str, Any]:
    if pull.get("number") != number or not isinstance(pull.get("isDraft"), bool):
        raise FleetCollectorError("GraphQL Pull Request identity is invalid")
    sha = pull.get("headRefOid")
    if sha != expected_sha or not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
        raise FleetCollectorError("GraphQL Pull Request head moved or is invalid")
    head_repo = _obj(pull.get("headRepository"), "GraphQL headRepository")
    base_repo = _obj(pull.get("baseRepository"), "GraphQL baseRepository")
    if (
        str(head_repo.get("nameWithOwner") or "").casefold()
        != repository.casefold()
        or str(base_repo.get("nameWithOwner") or "").casefold()
        != repository.casefold()
    ):
        raise FleetCollectorError("GraphQL Pull Request repository identity mismatch")
    state, merged = _normalize_state(
        pull.get("state"),
        pull.get("merged"),
        "GraphQL Pull Request",
    )
    return {
        "state": state,
        "merged": merged,
        "draft": pull["isDraft"],
        "sha": sha,
        "updated_at": _timestamp(
            pull.get("updatedAt"),
            "GraphQL Pull Request updatedAt",
        ),
    }


def _pull(data: Mapping[str, Any], project: Mapping[str, Any]) -> dict[str, Any]:
    repository = project["repository"]
    number = project["pull_request"]
    if (
        data.get("number") != number
        or data.get("state") not in {"open", "closed"}
        or not isinstance(data.get("draft"), bool)
    ):
        raise FleetCollectorError(
            f"GitHub returned invalid Pull Request evidence for {repository}"
        )
    merged_value = data.get("merged")
    merged_at = data.get("merged_at")
    if merged_value is None:
        merged = merged_at is not None
    elif isinstance(merged_value, bool):
        merged = merged_value
    else:
        raise FleetCollectorError(
            f"GitHub returned invalid merged evidence for {repository}"
        )
    if (merged and data["state"] != "closed") or (
        merged_value is False and merged_at is not None
    ):
        raise FleetCollectorError(
            f"GitHub returned contradictory merged evidence for {repository}"
        )
    head = _obj(data.get("head"), "Pull Request head")
    base = _obj(data.get("base"), "Pull Request base")
    head_repo = _obj(head.get("repo"), "Pull Request head repository")
    base_repo = _obj(base.get("repo"), "Pull Request base repository")
    sha = head.get("sha")
    if (
        not isinstance(sha, str)
        or not SHA_RE.fullmatch(sha)
        or str(head_repo.get("full_name") or "").casefold()
        != repository.casefold()
        or str(base_repo.get("full_name") or "").casefold()
        != repository.casefold()
    ):
        raise FleetCollectorError(
            f"GitHub returned unsafe same-repository head evidence for {repository}"
        )
    return {
        "state": data["state"],
        "merged": merged,
        "draft": data["draft"],
        "sha": sha,
        "updated_at": _timestamp(
            data.get("updated_at"),
            f"Pull Request updated_at for {repository}",
        ),
    }


def _run_state(run: Mapping[str, Any], repository: str) -> str:
    status = run.get("status")
    conclusion = run.get("conclusion")
    if status in {"requested", "waiting", "pending", "queued"}:
        return "queued"
    if status == "in_progress":
        return "in_progress"
    if status != "completed":
        raise FleetCollectorError(
            f"GitHub returned unsupported workflow status for {repository}"
        )
    if conclusion == "startup_failure":
        return "failure"
    if conclusion not in CHECK_STATES or conclusion in {
        "queued",
        "in_progress",
        "missing",
    }:
        raise FleetCollectorError(
            f"GitHub returned unsupported workflow conclusion for {repository}"
        )
    return str(conclusion)


def _checks(
    data: Mapping[str, Any],
    project: Mapping[str, Any],
    sha: str,
) -> tuple[dict[str, str], tuple[str, ...]]:
    repository = project["repository"]
    required = set(project["required_workflows"])
    runs = data.get("workflow_runs")
    total = data.get("total_count")
    if (
        not isinstance(runs, list)
        or isinstance(total, bool)
        or not isinstance(total, int)
        or total != len(runs)
        or total > MAX_RUNS
    ):
        raise FleetCollectorError(
            f"GitHub returned incomplete exact-head workflow evidence for {repository}"
        )
    selected: dict[str, tuple[datetime, int, str, str]] = {}
    for index, value in enumerate(runs):
        run = _obj(value, f"workflow_runs[{index}]")
        run_repository = _obj(
            run.get("repository"),
            f"workflow_runs[{index}].repository",
        )
        if (
            run.get("head_sha") != sha
            or run.get("event") != "pull_request"
            or str(run_repository.get("full_name") or "").casefold()
            != repository.casefold()
        ):
            raise FleetCollectorError(
                f"GitHub returned workflow evidence for a different identity in {repository}"
            )
        name = run.get("name")
        if name not in required:
            continue
        run_id = run.get("id")
        if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
            raise FleetCollectorError(
                f"GitHub returned invalid workflow id for {repository}"
            )
        updated = _timestamp(
            run.get("updated_at") or run.get("created_at"),
            f"workflow updated_at for {repository}",
        )
        evidence = (_dt(updated), run_id, _run_state(run, repository), updated)
        if name not in selected or evidence[:2] > selected[name][:2]:
            selected[name] = evidence
    checks: dict[str, str] = {}
    timestamps: list[str] = []
    for name in project["required_workflows"]:
        if name in selected:
            checks[name] = selected[name][2]
            timestamps.append(selected[name][3])
        else:
            checks[name] = "missing"
    return checks, tuple(timestamps)


def _review_evidence(
    data: Mapping[str, Any],
    project: Mapping[str, Any],
    sha: str,
) -> tuple[str, int, tuple[str, ...]]:
    threads = data.get("threads")
    comments = data.get("comments")
    if not isinstance(threads, list) or not isinstance(comments, list):
        raise FleetCollectorError("review evidence is incomplete")

    unresolved = 0
    timestamps: list[str] = []
    for index, value in enumerate(threads):
        thread = _obj(value, f"reviewThreads[{index}]")
        resolved = thread.get("isResolved")
        if not isinstance(resolved, bool):
            raise FleetCollectorError("review thread resolution evidence is invalid")
        if not resolved:
            unresolved += 1
        connection = _obj(
            thread.get("comments"),
            f"reviewThreads[{index}].comments",
        )
        nodes = connection.get("nodes")
        if not isinstance(nodes, list) or len(nodes) > 1:
            raise FleetCollectorError("review thread timestamp evidence is invalid")
        for node in nodes:
            comment = _obj(node, "review thread latest comment")
            timestamps.append(
                _timestamp(comment.get("updatedAt"), "review thread updatedAt")
            )

    trusted = {login.casefold() for login in project["trusted_coordinators"]}
    general_clean = 0
    general_blocked = 0
    pass_clean = {"scope-security": 0, "correctness-race": 0}
    pass_blocked = {"scope-security": 0, "correctness-race": 0}
    saw_current_reference = False
    ambiguous = False

    for index, value in enumerate(comments):
        comment = _obj(value, f"coordinator comments[{index}]")
        body = comment.get("body")
        author = comment.get("author")
        created = _timestamp(
            comment.get("createdAt"),
            f"coordinator comments[{index}].createdAt",
        )
        updated = _timestamp(
            comment.get("updatedAt"),
            f"coordinator comments[{index}].updatedAt",
        )
        if not isinstance(body, str) or len(body) > 20000 or "\x00" in body:
            raise FleetCollectorError("coordinator review comment body is invalid")
        login = (
            str(author.get("login") or "")
            if isinstance(author, dict)
            else ""
        )
        if login.casefold() not in trusted or created != updated:
            continue
        current_reference = f"foundation-coordinator-review:{sha}:" in body
        if current_reference:
            saw_current_reference = True
        recognized_spans: list[tuple[int, int]] = []
        for match in GENERAL_MARKER_RE.finditer(body):
            if match.group(1) != sha:
                continue
            recognized_spans.append(match.span())
            if match.group(2) == "clean":
                general_clean += 1
            else:
                general_blocked += 1
        for match in PASS_MARKER_RE.finditer(body):
            if match.group(1) != sha:
                continue
            recognized_spans.append(match.span())
            stage = match.group(2)
            if match.group(3) == "clean":
                pass_clean[stage] += 1
            else:
                pass_blocked[stage] += 1
        if current_reference and not recognized_spans:
            ambiguous = True
        if recognized_spans:
            remainder = body
            for start, end in reversed(sorted(recognized_spans)):
                remainder = remainder[:start] + remainder[end:]
            if len(remainder.strip()) < 20:
                ambiguous = True
            timestamps.append(updated)

    blocked_count = (
        general_blocked
        + pass_blocked["scope-security"]
        + pass_blocked["correctness-race"]
    )
    risk = project["risk_tier"]
    if blocked_count:
        return "blocked", unresolved, tuple(sorted(set(timestamps), key=_dt))
    if unresolved:
        return "pending", unresolved, tuple(sorted(set(timestamps), key=_dt))
    if risk == "protected":
        clean = (
            pass_clean["scope-security"] == 1
            and pass_clean["correctness-race"] == 1
            and general_clean == 0
            and not ambiguous
        )
        if clean:
            return "clean", unresolved, tuple(sorted(set(timestamps), key=_dt))
        saw_any = saw_current_reference or any(pass_clean.values()) or general_clean > 0
        return (
            "pending" if saw_any else "required",
            unresolved,
            tuple(sorted(set(timestamps), key=_dt)),
        )

    clean = general_clean == 1 and not any(pass_clean.values()) and not ambiguous
    if clean:
        return "clean", unresolved, tuple(sorted(set(timestamps), key=_dt))
    saw_any = saw_current_reference or general_clean > 0 or any(pass_clean.values())
    return (
        "pending" if saw_any else "required",
        unresolved,
        tuple(sorted(set(timestamps), key=_dt)),
    )


def _status(
    project: Mapping[str, Any],
    pull: Mapping[str, Any],
    checks: Mapping[str, str],
    review_state: str,
    unresolved_threads: int,
) -> tuple[str, str | None, bool]:
    configured_blocker = project["blocker"]
    if pull["merged"]:
        if project["human_action_required"]:
            raise FleetCollectorError(
                f"configured human action conflicts with merged PR in {project['repository']}"
            )
        return "completed", None, False
    if pull["state"] == "closed":
        if project["human_action_required"]:
            raise FleetCollectorError(
                f"configured human action conflicts with closed PR in {project['repository']}"
            )
        return (
            "blocked",
            configured_blocker or "Configured Pull Request is closed without merge",
            False,
        )
    if project["human_action_required"]:
        return "human_action", configured_blocker, True

    states = set(checks.values())
    detail = ", ".join(
        f"{name}={state}"
        for name, state in checks.items()
        if state not in PASSING_CHECK_STATES
    )
    if states & BAD_CHECKS:
        return (
            "fix_required",
            configured_blocker or f"Required exact-head workflow failed: {detail}",
            False,
        )
    if states & BLOCKED_CHECKS:
        return (
            "blocked",
            configured_blocker
            or f"Required exact-head workflow evidence is blocked: {detail}",
            False,
        )
    if configured_blocker is not None:
        return "blocked", configured_blocker, False
    if states & PENDING_CHECKS:
        return "ci_running", None, False
    if review_state == "blocked":
        return "blocked", "Coordinator review reported a blocking finding", False
    if review_state in {"required", "pending"} or unresolved_threads:
        return "review_required", None, False
    if review_state != "clean":
        raise FleetCollectorError(
            f"review state is not merge-ready for {project['repository']}"
        )
    return ("pr_open", None, False) if pull["draft"] else (
        "ready_to_merge",
        None,
        False,
    )


def _stable_connected_evidence(
    project: Mapping[str, Any],
    api: GitHubApi,
) -> tuple[dict[str, Any], dict[str, str], tuple[str, ...], str, int, tuple[str, ...]]:
    repository = project["repository"]
    number = project["pull_request"]
    initial_pull = _pull(api.get_pull(repository, number), project)
    initial_checks, initial_workflow_times = _checks(
        api.get_workflow_runs(repository, initial_pull["sha"]),
        project,
        initial_pull["sha"],
    )
    initial_review_data = api.get_review_evidence(
        repository,
        number,
        initial_pull["sha"],
    )
    if initial_review_data.get("snapshot") != initial_pull:
        raise FleetCollectorError(
            f"GraphQL and REST Pull Request evidence disagree for {repository}"
        )
    initial_review = _review_evidence(
        initial_review_data,
        project,
        initial_pull["sha"],
    )

    final_checks, final_workflow_times = _checks(
        api.get_workflow_runs(repository, initial_pull["sha"]),
        project,
        initial_pull["sha"],
    )
    final_review_data = api.get_review_evidence(
        repository,
        number,
        initial_pull["sha"],
    )
    if final_review_data.get("snapshot") != initial_pull:
        raise FleetCollectorError(
            f"Pull Request evidence moved during final review collection for {repository}"
        )
    final_review = _review_evidence(
        final_review_data,
        project,
        initial_pull["sha"],
    )
    final_pull = _pull(api.get_pull(repository, number), project)

    if final_pull != initial_pull:
        raise FleetCollectorError(
            f"Pull Request evidence moved during collection for {repository}"
        )
    if (
        final_checks != initial_checks
        or final_workflow_times != initial_workflow_times
    ):
        raise FleetCollectorError(
            f"exact-head workflow evidence moved during collection for {repository}"
        )
    if final_review != initial_review:
        raise FleetCollectorError(
            f"coordinator review evidence moved during collection for {repository}"
        )
    return (
        final_pull,
        final_checks,
        final_workflow_times,
        final_review[0],
        final_review[1],
        final_review[2],
    )


def collect_document(
    projects: Sequence[Mapping[str, Any]],
    api: GitHubApi,
    *,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    generated = _now(now or (lambda: datetime.now(timezone.utc)))
    records: list[dict[str, Any]] = []
    for project in projects:
        if project["pull_request"] is None:
            records.append(
                {
                    "repository": project["repository"],
                    "phase": project["phase"],
                    "issue": project["issue"],
                    "pull_request": None,
                    "status": project["baseline_status"],
                    "head_sha": None,
                    "checks": {},
                    "implementation_route": project["implementation_route"],
                    "risk_tier": project["risk_tier"],
                    "review_route": "github-coordinator",
                    "review_state": "required",
                    "unresolved_review_threads": 0,
                    "next_action": project["next_action"],
                    "blocker": None,
                    "human_action_required": False,
                    "updated_at": generated,
                }
            )
            continue

        (
            pull,
            checks,
            workflow_times,
            review_state,
            unresolved,
            review_times,
        ) = _stable_connected_evidence(project, api)
        status, blocker, human_action = _status(
            project,
            pull,
            checks,
            review_state,
            unresolved,
        )
        updated_at = _max_timestamp(
            [pull["updated_at"], *workflow_times, *review_times],
            pull["updated_at"],
        )
        records.append(
            {
                "repository": project["repository"],
                "phase": project["phase"],
                "issue": project["issue"],
                "pull_request": project["pull_request"],
                "status": status,
                "head_sha": pull["sha"],
                "checks": dict(
                    sorted(checks.items(), key=lambda item: item[0].casefold())
                ),
                "implementation_route": project["implementation_route"],
                "risk_tier": project["risk_tier"],
                "review_route": "github-coordinator",
                "review_state": review_state,
                "unresolved_review_threads": unresolved,
                "next_action": project["next_action"],
                "blocker": blocker,
                "human_action_required": human_action,
                "updated_at": updated_at,
            }
        )

    document = {
        "schema_version": 2,
        "generated_at": generated,
        "projects": sorted(
            records,
            key=lambda item: item["repository"].casefold(),
        ),
    }
    try:
        validate_document(document)
    except FleetProgressError as exc:
        raise FleetCollectorError(
            f"generated Fleet Progress document failed validation: {exc}"
        ) from exc
    return document


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect exact-head GitHub evidence for Fleet Progress JSON."
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check-config", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.check_config and args.output is not None:
        parser.error("--check-config cannot be combined with --output")
    try:
        projects = load_config(args.input)
        if args.check_config:
            print(f"valid: {len(projects)} project configurations")
            return 0
        payload = (
            json.dumps(
                collect_document(
                    projects,
                    GitHubApi(resolve_token(os.environ)),
                ),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        if args.output is None:
            sys.stdout.write(payload)
        else:
            args.output.write_text(payload, encoding="utf-8")
        return 0
    except FleetCollectorError as exc:
        print(f"fleet-collect-github: error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(
            "fleet-collect-github: error: cannot write output file: "
            f"{exc.strerror or 'I/O error'}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

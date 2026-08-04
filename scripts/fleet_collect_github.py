#!/usr/bin/env python3
"""Create Fleet Progress JSON from exact-head, read-only GitHub evidence."""
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
        AUDITORS, AUDIT_STATES, CHECK_NAME_RE, CHECK_STATES,
        IMPLEMENTATION_ROUTES, PASSING_CHECK_STATES, REPOSITORY_RE,
        RISK_TIERS, FleetProgressError, validate_document,
    )
except ImportError:
    from fleet_progress import (  # type: ignore[no-redef]
        AUDITORS, AUDIT_STATES, CHECK_NAME_RE, CHECK_STATES,
        IMPLEMENTATION_ROUTES, PASSING_CHECK_STATES, REPOSITORY_RE,
        RISK_TIERS, FleetProgressError, validate_document,
    )

API_BASE = "https://api.github.com"
MAX_PROJECTS = 50
MAX_WORKFLOWS = 16
MAX_INPUT = 256 * 1024
MAX_RESPONSE = 2 * 1024 * 1024
TIMEOUT = 15
BASELINES = frozenset({"backlog", "ready", "completed", "idle"})
BAD = frozenset({"failure", "cancelled", "timed_out", "stale"})
PENDING = frozenset({"queued", "in_progress"})
BLOCKED_CHECKS = frozenset({"missing", "action_required"})
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
PROJECT_KEYS = frozenset({
    "repository", "phase", "issue", "pull_request", "required_workflows",
    "implementation_route", "risk_tier", "selected_auditor", "audit_state",
    "next_action", "blocker", "human_action_required", "baseline_status",
})


class FleetCollectorError(ValueError):
    pass


def _obj(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise FleetCollectorError(f"{where} must be an object")
    return value


def _keys(value: Mapping[str, Any], expected: frozenset[str], where: str) -> None:
    missing, extra = sorted(expected - value.keys()), sorted(value.keys() - expected)
    if missing:
        raise FleetCollectorError(f"{where} is missing fields: {', '.join(missing)}")
    if extra:
        raise FleetCollectorError(f"{where} has unsupported fields: {', '.join(extra)}")


def _text(value: Any, where: str, limit: int = 500) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FleetCollectorError(f"{where} must be a non-empty string")
    value = value.strip()
    if len(value) > limit or CONTROL_RE.search(value):
        raise FleetCollectorError(f"{where} is not bounded safe text")
    return value


def _enum(value: Any, allowed: frozenset[str], where: str) -> str:
    value = _text(value, where, 80)
    if value not in allowed:
        raise FleetCollectorError(f"{where} has an unsupported value")
    return value


def _number(value: Any, where: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise FleetCollectorError(f"{where} must be a positive integer or null")
    return value


def _timestamp(value: Any, where: str) -> str:
    value = _text(value, where, 40)
    if not UTC_RE.fullmatch(value):
        raise FleetCollectorError(f"{where} must be an ISO-8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise FleetCollectorError(f"{where} must be a valid UTC timestamp") from exc
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value[:-1] + "+00:00")


def _now(clock: Callable[[], datetime]) -> str:
    return clock().astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def validate_config(value: Any) -> tuple[dict[str, Any], ...]:
    document = _obj(value, "document")
    _keys(document, frozenset({"schema_version", "projects"}), "document")
    if document["schema_version"] != 1:
        raise FleetCollectorError("document.schema_version must equal 1")
    raw = document["projects"]
    if not isinstance(raw, list) or len(raw) > MAX_PROJECTS:
        raise FleetCollectorError(f"document.projects must contain at most {MAX_PROJECTS} items")

    projects: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        where = f"projects[{index}]"
        project = _obj(item, where)
        _keys(project, PROJECT_KEYS, where)
        repo = _text(project["repository"], f"{where}.repository", 200)
        if not REPOSITORY_RE.fullmatch(repo):
            raise FleetCollectorError(f"{where}.repository must use owner/name form")
        if repo.casefold() in seen:
            raise FleetCollectorError(f"document.projects contains duplicate repository {repo}")
        seen.add(repo.casefold())

        workflows = project["required_workflows"]
        if not isinstance(workflows, list) or len(workflows) > MAX_WORKFLOWS:
            raise FleetCollectorError(f"{where}.required_workflows is invalid")
        normalized_workflows: list[str] = []
        workflow_seen: set[str] = set()
        for workflow_index, workflow in enumerate(workflows):
            name = _text(workflow, f"{where}.required_workflows[{workflow_index}]", 120)
            if not CHECK_NAME_RE.fullmatch(name) or name.casefold() in workflow_seen:
                raise FleetCollectorError(f"{where}.required_workflows contains duplicate workflow")
            workflow_seen.add(name.casefold())
            normalized_workflows.append(name)

        pull_request = _number(project["pull_request"], f"{where}.pull_request")
        blocker = None if project["blocker"] is None else _text(project["blocker"], f"{where}.blocker")
        human = project["human_action_required"]
        if not isinstance(human, bool):
            raise FleetCollectorError(f"{where}.human_action_required must be boolean")
        baseline = None if project["baseline_status"] is None else _enum(project["baseline_status"], BASELINES, f"{where}.baseline_status")
        auditor = _enum(project["selected_auditor"], AUDITORS, f"{where}.selected_auditor")
        audit = _enum(project["audit_state"], AUDIT_STATES, f"{where}.audit_state")
        risk = _enum(project["risk_tier"], RISK_TIERS, f"{where}.risk_tier")
        if audit == "not-required" and auditor != "none":
            raise FleetCollectorError(f"{where}.selected_auditor conflicts with audit state")
        if audit in {"pending", "clean", "blocked"} and auditor == "none":
            raise FleetCollectorError(f"{where}.selected_auditor is required")
        if risk in {"standard", "protected"} and audit == "not-required":
            raise FleetCollectorError(f"{where}.audit_state conflicts with risk tier")
        if (human or audit == "blocked") and blocker is None:
            raise FleetCollectorError(f"{where}.blocker is required")
        if pull_request is None:
            if normalized_workflows or baseline is None or human or blocker is not None:
                raise FleetCollectorError(f"{where} has contradictory non-PR state")
        elif not normalized_workflows or baseline is not None:
            raise FleetCollectorError(f"{where} has contradictory Pull Request state")

        projects.append({
            "repository": repo,
            "phase": _text(project["phase"], f"{where}.phase", 160),
            "issue": _number(project["issue"], f"{where}.issue"),
            "pull_request": pull_request,
            "required_workflows": tuple(sorted(normalized_workflows, key=str.casefold)),
            "implementation_route": _enum(project["implementation_route"], IMPLEMENTATION_ROUTES, f"{where}.implementation_route"),
            "risk_tier": risk,
            "selected_auditor": auditor,
            "audit_state": audit,
            "next_action": _text(project["next_action"], f"{where}.next_action"),
            "blocker": blocker,
            "human_action_required": human,
            "baseline_status": baseline,
        })
    return tuple(sorted(projects, key=lambda item: item["repository"].casefold()))


def load_config(path: Path) -> tuple[dict[str, Any], ...]:
    try:
        if path.stat().st_size > MAX_INPUT:
            raise FleetCollectorError("input file exceeded the bounded size limit")
        return validate_config(json.loads(path.read_text(encoding="utf-8")))
    except FleetCollectorError:
        raise
    except json.JSONDecodeError as exc:
        raise FleetCollectorError(f"input is not valid JSON at line {exc.lineno}, column {exc.colno}") from exc
    except OSError as exc:
        raise FleetCollectorError(f"cannot read input file: {exc.strerror or 'I/O error'}") from exc


def resolve_token(environment: Mapping[str, str]) -> str | None:
    first, second = environment.get("GH_TOKEN") or None, environment.get("GITHUB_TOKEN") or None
    if first is not None and second is not None and first != second:
        raise FleetCollectorError("GH_TOKEN and GITHUB_TOKEN are both set with different values")
    token = first or second
    if token is not None and (len(token) > 4096 or "\r" in token or "\n" in token):
        raise FleetCollectorError("GitHub token environment value is invalid")
    return token


class GitHubApi:
    """Fixed-host client that can only issue GET requests to /repos endpoints."""

    def __init__(self, token: str | None, opener: Callable[..., Any] | None = None) -> None:
        self.token = token
        self.opener = opener or urllib.request.urlopen

    def get(self, path: str, query: Mapping[str, str] | None = None) -> Mapping[str, Any]:
        if not path.startswith("/repos/"):
            raise FleetCollectorError("internal endpoint escaped the fixed /repos boundary")
        url = API_BASE + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "api.github.com":
            raise FleetCollectorError("internal endpoint escaped the fixed GitHub API host")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "foundation-fleet-collector/1",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with self.opener(request, timeout=TIMEOUT) as response:
                payload = response.read(MAX_RESPONSE + 1)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                detail = "authentication was rejected"
            elif exc.code == 403:
                detail = "rate limit is exhausted" if exc.headers and exc.headers.get("X-RateLimit-Remaining") == "0" else "access was forbidden"
            elif exc.code == 404:
                detail = "resource was not found or is not accessible"
            else:
                detail = f"request failed with HTTP {exc.code}"
            raise FleetCollectorError(f"GitHub API {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise FleetCollectorError("GitHub API network request failed") from exc
        if len(payload) > MAX_RESPONSE:
            raise FleetCollectorError("GitHub API response exceeded the bounded size limit")
        try:
            return _obj(json.loads(payload.decode("utf-8")), "GitHub API response")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FleetCollectorError("GitHub API returned invalid JSON") from exc

    def get_pull(self, repository: str, number: int) -> Mapping[str, Any]:
        owner, name = repository.split("/", 1)
        return self.get(f"/repos/{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(name, safe='')}/pulls/{number}")

    def get_workflow_runs(self, repository: str, sha: str) -> Mapping[str, Any]:
        owner, name = repository.split("/", 1)
        return self.get(
            f"/repos/{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(name, safe='')}/actions/runs",
            {"event": "pull_request", "head_sha": sha, "per_page": "100", "page": "1"},
        )


def _pull(data: Mapping[str, Any], project: Mapping[str, Any]) -> dict[str, Any]:
    repository, number = project["repository"], project["pull_request"]
    if data.get("number") != number or data.get("state") not in {"open", "closed"} or not isinstance(data.get("draft"), bool):
        raise FleetCollectorError(f"GitHub returned invalid Pull Request evidence for {repository}")
    merged_value, merged_at = data.get("merged"), data.get("merged_at")
    if merged_value is None:
        merged = merged_at is not None
    elif isinstance(merged_value, bool):
        merged = merged_value
    else:
        raise FleetCollectorError(f"GitHub returned invalid merged evidence for {repository}")
    if (merged and data["state"] != "closed") or (merged_value is False and merged_at is not None):
        raise FleetCollectorError(f"GitHub returned contradictory merged evidence for {repository}")
    head = _obj(data.get("head"), "Pull Request head")
    sha = head.get("sha")
    head_repo = _obj(head.get("repo"), "Pull Request head repository").get("full_name")
    if not isinstance(sha, str) or not SHA_RE.fullmatch(sha) or not isinstance(head_repo, str) or head_repo.casefold() != repository.casefold():
        raise FleetCollectorError(f"GitHub returned unsafe exact-head evidence for {repository}")
    return {
        "state": data["state"], "merged": merged, "draft": data["draft"],
        "sha": sha, "updated_at": _timestamp(data.get("updated_at"), f"Pull Request updated_at for {repository}"),
    }


def _run_state(run: Mapping[str, Any], repository: str) -> str:
    status, conclusion = run.get("status"), run.get("conclusion")
    if status in {"requested", "waiting", "pending", "queued"}:
        return "queued"
    if status == "in_progress":
        return "in_progress"
    if status != "completed":
        raise FleetCollectorError(f"GitHub returned unsupported workflow status for {repository}")
    if conclusion == "startup_failure":
        return "failure"
    if conclusion not in CHECK_STATES or conclusion in {"queued", "in_progress", "missing"}:
        raise FleetCollectorError(f"GitHub returned unsupported workflow conclusion for {repository}")
    return str(conclusion)


def _checks(data: Mapping[str, Any], project: Mapping[str, Any], sha: str) -> tuple[dict[str, str], list[str]]:
    repository, required = project["repository"], set(project["required_workflows"])
    runs, total = data.get("workflow_runs"), data.get("total_count")
    if not isinstance(runs, list) or isinstance(total, bool) or not isinstance(total, int) or total != len(runs) or total > 100:
        raise FleetCollectorError(f"GitHub returned incomplete exact-head workflow evidence for {repository}")
    selected: dict[str, tuple[datetime, int, str, str]] = {}
    for index, value in enumerate(runs):
        run = _obj(value, f"workflow_runs[{index}]")
        if run.get("head_sha") != sha or run.get("event") != "pull_request":
            raise FleetCollectorError(f"GitHub returned workflow evidence for a different head in {repository}")
        name = run.get("name")
        if name not in required:
            continue
        run_id = run.get("id")
        if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
            raise FleetCollectorError(f"GitHub returned invalid workflow id for {repository}")
        updated = _timestamp(run.get("updated_at") or run.get("created_at"), f"workflow updated_at for {repository}")
        evidence = (_dt(updated), run_id, _run_state(run, repository), updated)
        if name not in selected or evidence[:2] > selected[name][:2]:
            selected[name] = evidence
    checks, timestamps = {}, []
    for name in project["required_workflows"]:
        if name in selected:
            checks[name], updated = selected[name][2], selected[name][3]
            timestamps.append(updated)
        else:
            checks[name] = "missing"
    return checks, timestamps


def _status(project: Mapping[str, Any], pull: Mapping[str, Any], checks: Mapping[str, str]) -> tuple[str, str | None, bool]:
    blocker = project["blocker"]
    if pull["merged"]:
        if project["human_action_required"]:
            raise FleetCollectorError(f"configured human action conflicts with merged PR in {project['repository']}")
        return "completed", None, False
    if pull["state"] == "closed":
        if project["human_action_required"]:
            raise FleetCollectorError(f"configured human action conflicts with closed PR in {project['repository']}")
        return "blocked", blocker or "Configured Pull Request is closed without merge", False
    if project["human_action_required"]:
        return "human_action", blocker, True
    states = set(checks.values())
    detail = ", ".join(f"{name}={state}" for name, state in checks.items() if state not in PASSING_CHECK_STATES)
    if states & BAD:
        return "fix_required", blocker or f"Required exact-head workflow failed: {detail}", False
    if states & BLOCKED_CHECKS:
        return "blocked", blocker or f"Required exact-head workflow evidence is blocked: {detail}", False
    if blocker is not None:
        return "blocked", blocker, False
    if states & PENDING:
        return "ci_running", None, False
    if project["audit_state"] == "blocked":
        return "blocked", blocker, False
    if project["audit_state"] == "route-unavailable":
        return "blocked", "Selected external audit route is unavailable", False
    if project["audit_state"] in {"required", "pending"}:
        return "review_required", None, False
    audit_ready = project["audit_state"] == "clean" or (project["risk_tier"] == "low" and project["audit_state"] == "not-required")
    if not audit_ready:
        raise FleetCollectorError(f"audit state is not merge-ready for {project['repository']}")
    return ("review_required", None, False) if pull["draft"] else ("ready_to_merge", None, False)


def collect_document(projects: Sequence[Mapping[str, Any]], api: GitHubApi, *, now: Callable[[], datetime] | None = None) -> dict[str, Any]:
    generated = _now(now or (lambda: datetime.now(timezone.utc)))
    records = []
    for project in projects:
        if project["pull_request"] is None:
            records.append({
                "repository": project["repository"], "phase": project["phase"], "issue": project["issue"],
                "pull_request": None, "status": project["baseline_status"], "head_sha": None, "checks": {},
                "implementation_route": project["implementation_route"], "risk_tier": project["risk_tier"],
                "selected_auditor": project["selected_auditor"], "audit_state": project["audit_state"],
                "next_action": project["next_action"], "blocker": None, "human_action_required": False,
                "updated_at": generated,
            })
            continue
        pull = _pull(api.get_pull(project["repository"], project["pull_request"]), project)
        checks, workflow_times = _checks(api.get_workflow_runs(project["repository"], pull["sha"]), project, pull["sha"])
        status, blocker, human = _status(project, pull, checks)
        updated = max([pull["updated_at"], *workflow_times], key=_dt)
        records.append({
            "repository": project["repository"], "phase": project["phase"], "issue": project["issue"],
            "pull_request": project["pull_request"], "status": status, "head_sha": pull["sha"],
            "checks": dict(sorted(checks.items(), key=lambda item: item[0].casefold())),
            "implementation_route": project["implementation_route"], "risk_tier": project["risk_tier"],
            "selected_auditor": project["selected_auditor"], "audit_state": project["audit_state"],
            "next_action": project["next_action"], "blocker": blocker, "human_action_required": human,
            "updated_at": updated,
        })
    document = {"schema_version": 1, "generated_at": generated, "projects": sorted(records, key=lambda item: item["repository"].casefold())}
    try:
        validate_document(document)
    except FleetProgressError as exc:
        raise FleetCollectorError(f"generated Fleet Progress document failed validation: {exc}") from exc
    return document


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect exact-head GitHub evidence for Fleet Progress JSON.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check-config", action="store_true")
    args = parser.parse_args(argv)
    if args.check_config and args.output is not None:
        parser.error("--check-config cannot be combined with --output")
    try:
        projects = load_config(args.input)
        if args.check_config:
            print(f"valid: {len(projects)} project configurations")
            return 0
        payload = json.dumps(collect_document(projects, GitHubApi(resolve_token(os.environ))), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.output is None:
            sys.stdout.write(payload)
        else:
            args.output.write_text(payload, encoding="utf-8")
        return 0
    except FleetCollectorError as exc:
        print(f"fleet-collect-github: error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"fleet-collect-github: error: cannot write output file: {exc.strerror or 'I/O error'}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

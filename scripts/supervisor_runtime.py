#!/usr/bin/env python3
"""Trusted default-branch supervisor and immutable run/job attestation runtime.

Write-capable jobs execute this module only from the repository default branch.
Candidate code is executed only by read-only jobs in the fixed trusted workflow.
GitHub-owned workflow-run and workflow-job records are the attestation source; this
module never relies on candidate-authored statuses or custom check runs.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Iterable

from scripts.supervisor_policy import (
    is_protected,
    parse_issue_number,
    protected_scope_is_authorized,
)

REPO = os.environ["REPOSITORY"]
DEFAULT_BRANCH = os.environ["DEFAULT_BRANCH"]
AUTOMATION_OWNER = os.environ["AUTOMATION_OWNER"]
REPOSITORY_OWNER = REPO.split("/", 1)[0]
TRUSTED_ISSUE_AUTHORS = {AUTOMATION_OWNER, REPOSITORY_OWNER}
ATTESTATION_JOB_NAMES = ("CI / validate", "Unit Tests / test")
ATTESTATION_NAMES = ATTESTATION_JOB_NAMES
MAX_CANDIDATES = 10
MAX_CHANGED_FILES = 100
MAX_ATTESTATION_ATTEMPTS = 3
NO_PROGRESS_MINUTES = 60
ALLOWED_PREFIXES = ("claude-issue-", "automation/", "fix/")
ALLOWED_AUTHORS = {*TRUSTED_ISSUE_AUTHORS, "github-actions[bot]"}
TRUSTED_WORKFLOW_PATH = ".github/workflows/trusted-checks.yml"
AUDIT_WORKFLOWS = (
    "trusted-checks.yml",
    "ci-reconcile.yml",
    "supervisor.yml",
    "claude-queue.yml",
)
INTERNAL_STOP_BRANCH = "automation-internal-stops"
INTERNAL_STOP_ROOT = "automation-stops"
CODEX_LOGIN = "chatgpt-codex-connector[bot]"
ACTIONS_LOGIN = "github-actions[bot]"
HUMAN_NOTICE_PREFIX = "<!-- foundation-human-only:"
E2E_AUTO_CLOSE_MARKER = "<!-- foundation-e2e-auto-close -->"
ACTIVE_RUN_STATES = {"queued", "in_progress", "waiting", "pending", "requested"}
EXACT_SHA = re.compile(r"^[0-9a-f]{40}$")
SAFE_REASON = re.compile(r"^[A-Z0-9_]+$")

HUMAN_ONLY_ACTIONS = {
    "HUMAN_ONLY_ACCOUNT_LEVEL_REPOSITORY_CREATION_UI_UNAVAILABLE": (
        "Create and connect the exact missing public repositories in the GitHub account-level UI."
    ),
    "HUMAN_ONLY_CREDENTIAL_PROVIDER_UI_REQUIRED": (
        "Complete the required credential, MFA, CAPTCHA, or hardware-key step in the provider UI."
    ),
    "HUMAN_ONLY_DISCONNECTED_INTEGRATION_RECONNECTION_UI_REQUIRED": (
        "Reconnect the named integration in its provider or ChatGPT connection UI."
    ),
}
HUMAN_ONLY_REASONS = frozenset(HUMAN_ONLY_ACTIONS)
INTERNAL_STOP_REASONS_THAT_MUST_NOT_NOTIFY = frozenset(
    {
        "TRUSTED_ATTESTATION_RETRY_EXHAUSTED",
        "NO_MEANINGFUL_PROGRESS",
        "MISSING_TRUSTED_SOURCE_ISSUE",
        "UNTRUSTED_SOURCE_ISSUE",
        "INCOMPLETE_CHANGED_FILE_EVIDENCE",
        "UNAUTHORIZED_PROTECTED_PATH",
        "UNTRUSTED_EVIDENCE",
        "BLOCKING_CODEX_REVIEW",
        "MERGE_NOT_READY",
        "AMBIGUOUS_TECHNICAL_STATE",
    }
)


def gh(*args: str) -> str:
    return subprocess.check_output(["gh", *args], text=True)


def gh_result(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["gh", *args],
        text=True,
        capture_output=True,
        check=False,
    )


def api(path: str) -> Any:
    return json.loads(gh("api", "-H", "Accept: application/vnd.github+json", path))


def api_list(path: str) -> list[dict[str, Any]]:
    pages = json.loads(
        gh(
            "api",
            "-H",
            "Accept: application/vnd.github+json",
            "--paginate",
            "--slurp",
            path,
        )
    )
    items: list[dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, list):
            raise RuntimeError(f"Expected list page from {path}")
        items.extend(page)
    return items


def api_key_pages(path: str, key: str) -> list[dict[str, Any]]:
    pages = json.loads(
        gh(
            "api",
            "-H",
            "Accept: application/vnd.github+json",
            "--paginate",
            "--slurp",
            path,
        )
    )
    items: list[dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, dict):
            raise RuntimeError(f"Expected object page from {path}")
        values = page.get(key) or []
        if not isinstance(values, list):
            raise RuntimeError(f"Expected list key {key} from {path}")
        items.extend(values)
    return items


def current_default_sha() -> str:
    return str(api(f"repos/{REPO}/commits/{DEFAULT_BRANCH}")["sha"])


@lru_cache(maxsize=1)
def trusted_workflow_id() -> int:
    return int(api(f"repos/{REPO}/actions/workflows/trusted-checks.yml")["id"])


@lru_cache(maxsize=128)
def workflow_run(run_id: int) -> dict[str, Any]:
    return api(f"repos/{REPO}/actions/runs/{run_id}")


def comment(number: int, body: str) -> None:
    gh("issue", "comment", str(number), "--repo", REPO, "--body", body)


def ensure_label(number: int, label: str, color: str, description: str) -> None:
    gh(
        "label",
        "create",
        label,
        "--repo",
        REPO,
        "--color",
        color,
        "--description",
        description,
        "--force",
    )
    gh("issue", "edit", str(number), "--repo", REPO, "--add-label", label)


def remove_label(number: int, label: str) -> None:
    subprocess.run(
        ["gh", "issue", "edit", str(number), "--repo", REPO, "--remove-label", label],
        check=False,
        capture_output=True,
        text=True,
    )


def _require_positive_number(name: str, value: int | None) -> int:
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _require_exact_sha(sha: str) -> str:
    if not EXACT_SHA.fullmatch(sha):
        raise ValueError("exact_head_sha must be a lowercase 40-character commit SHA")
    return sha


def _normalized_evidence(values: Iterable[str], name: str) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(item.strip() for item in values if item.strip()))
    if not normalized:
        raise ValueError(f"{name} must contain concrete nonempty evidence")
    return normalized


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def minutes_since(value: str | None, now: datetime | None = None) -> int | None:
    observed = _parse_timestamp(value)
    if observed is None:
        return None
    current = now or datetime.now(timezone.utc)
    return max(0, int((current - observed).total_seconds() // 60))


def minutes_without_progress(updated_at: str, now: datetime | None = None) -> int:
    """Compatibility helper; runtime decisions use immutable evidence timestamps."""
    value = minutes_since(updated_at, now)
    return value if value is not None else 0


def _live_pr(pr_number: int, expected_sha: str) -> dict[str, Any]:
    live = api(f"repos/{REPO}/pulls/{pr_number}")
    live_sha = _require_exact_sha(str((live.get("head") or {}).get("sha") or ""))
    if live_sha != expected_sha:
        raise RuntimeError("Pull Request head moved during exact-SHA validation")
    return live


def unresolved_review_threads(pr_number: int) -> bool:
    owner, name = REPO.split("/", 1)
    query = """
    query($owner:String!,$name:String!,$number:Int!,$cursor:String){
      repository(owner:$owner,name:$name){
        pullRequest(number:$number){
          reviewThreads(first:100,after:$cursor){
            nodes{isResolved}
            pageInfo{hasNextPage endCursor}
          }
        }
      }
    }
    """
    cursor: str | None = None
    while True:
        args = [
            "api",
            "graphql",
            "-f",
            f"query={query}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"number={pr_number}",
        ]
        if cursor:
            args.extend(["-F", f"cursor={cursor}"])
        payload = json.loads(gh(*args))
        threads = payload["data"]["repository"]["pullRequest"]["reviewThreads"]
        if any(not node.get("isResolved") for node in threads.get("nodes") or []):
            return True
        page_info = threads["pageInfo"]
        if not page_info.get("hasNextPage"):
            return False
        cursor = page_info.get("endCursor")
        if not cursor:
            raise RuntimeError("Review-thread pagination returned no end cursor")


def _codex_items(pr_number: int) -> list[dict[str, Any]]:
    return [
        *api_list(f"repos/{REPO}/issues/{pr_number}/comments?per_page=100"),
        *api_list(f"repos/{REPO}/pulls/{pr_number}/reviews?per_page=100"),
    ]


def exact_codex_evidence(pr_number: int, sha: str) -> dict[str, str | None]:
    short = sha[:10]
    marker = f"<!-- foundation-codex-request:{sha} -->"
    trusted_requests: list[dict[str, Any]] = []
    for item in reversed(_codex_items(pr_number)):
        body = item.get("body") or ""
        login = (item.get("user") or {}).get("login") or ""
        if login == CODEX_LOGIN and (sha in body or short in body):
            lower = body.lower()
            clean = "didn't find any major issues" in lower or "no major issues" in lower
            state = "clean" if clean and not unresolved_review_threads(pr_number) else "blocking"
            return {
                "state": state,
                "timestamp": item.get("submitted_at") or item.get("created_at"),
                "request_timestamp": None,
            }
        if (
            login == ACTIONS_LOGIN
            and marker in body
            and sha in body
            and item.get("created_at") == item.get("updated_at")
        ):
            trusted_requests.append(item)
    for request in trusted_requests:
        reactions = api_list(
            f"repos/{REPO}/issues/comments/{request['id']}/reactions?per_page=100"
        )
        clean_reactions = [
            reaction
            for reaction in reactions
            if (reaction.get("user") or {}).get("login") == CODEX_LOGIN
            and reaction.get("content") == "+1"
        ]
        if clean_reactions:
            latest = max(
                (reaction.get("created_at") or request.get("created_at") for reaction in clean_reactions),
                default=request.get("created_at"),
            )
            state = "blocking" if unresolved_review_threads(pr_number) else "clean"
            return {
                "state": state,
                "timestamp": latest,
                "request_timestamp": request.get("created_at"),
            }
    request_timestamp = max(
        (item.get("created_at") for item in trusted_requests if item.get("created_at")),
        default=None,
    )
    return {
        "state": "pending",
        "timestamp": None,
        "request_timestamp": request_timestamp,
    }


def exact_codex_state(pr_number: int, sha: str) -> str:
    return str(exact_codex_evidence(pr_number, sha)["state"])


def exact_codex_clean(pr_number: int, sha: str) -> bool:
    return exact_codex_state(pr_number, sha) == "clean"


def trusted_candidate(pr: dict[str, Any]) -> bool:
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    author = (pr.get("user") or {}).get("login") or ""
    return bool(
        ((head.get("repo") or {}).get("full_name") == REPO)
        and ((base.get("repo") or {}).get("full_name") == REPO)
        and base.get("ref") == DEFAULT_BRANCH
        and author in ALLOWED_AUTHORS
        and (head.get("ref") or "").startswith(ALLOWED_PREFIXES)
        and not any(
            label.get("name") == "ai-no-merge" for label in pr.get("labels") or []
        )
    )


def trusted_source_issue(issue: dict[str, Any]) -> bool:
    login = (issue.get("user") or {}).get("login") or ""
    return not issue.get("pull_request") and login in TRUSTED_ISSUE_AUTHORS


def trusted_attestation_run(
    run: dict[str, Any], sha: str, *, allow_active: bool
) -> bool:
    repository = run.get("repository") or {}
    actor = (run.get("actor") or {}).get("login") or ""
    path = str(run.get("path") or "")
    if repository.get("full_name") != REPO:
        return False
    if int(run.get("workflow_id") or 0) != trusted_workflow_id():
        return False
    if run.get("event") != "workflow_dispatch":
        return False
    if run.get("head_branch") != DEFAULT_BRANCH:
        return False
    if run.get("head_sha") != current_default_sha():
        return False
    if actor not in ALLOWED_AUTHORS:
        return False
    if path and TRUSTED_WORKFLOW_PATH not in path:
        return False
    if run.get("display_title") != f"Trusted checks {sha}":
        return False
    status = str(run.get("status") or "")
    if allow_active and status in ACTIVE_RUN_STATES:
        return True
    return status == "completed"


def trusted_runs_for_sha(sha: str) -> list[dict[str, Any]]:
    runs = api_key_pages(
        f"repos/{REPO}/actions/workflows/{trusted_workflow_id()}/runs"
        f"?branch={DEFAULT_BRANCH}&event=workflow_dispatch&per_page=100",
        "workflow_runs",
    )
    return [run for run in runs if trusted_attestation_run(run, sha, allow_active=True)]


def trusted_run_jobs(run_id: int) -> list[dict[str, Any]]:
    return api_key_pages(
        f"repos/{REPO}/actions/runs/{run_id}/jobs?filter=all&per_page=100",
        "jobs",
    )


def _complete_successful_job_set(
    jobs: list[dict[str, Any]], run_id: int, trusted_workflow_sha: str
) -> bool:
    by_name: dict[str, list[dict[str, Any]]] = {
        name: [] for name in ATTESTATION_JOB_NAMES
    }
    for job in jobs:
        name = str(job.get("name") or "")
        if name not in by_name:
            continue
        if int(job.get("run_id") or 0) != run_id:
            return False
        if job.get("head_sha") != trusted_workflow_sha:
            return False
        by_name[name].append(job)
    if any(len(matches) != 1 for matches in by_name.values()):
        return False
    return all(
        matches[0].get("status") == "completed"
        and matches[0].get("conclusion") == "success"
        for matches in by_name.values()
    )


def attestation_attempts(sha: str) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    for run in trusted_runs_for_sha(sha):
        run_id = int(run["id"])
        status = str(run.get("status") or "")
        active = status in ACTIVE_RUN_STATES
        complete = False
        success = False
        if not active:
            trusted_workflow_sha = str(run.get("head_sha") or "")
            jobs = trusted_run_jobs(run_id)
            complete = bool(
                trusted_workflow_sha
                and _complete_successful_job_set(jobs, run_id, trusted_workflow_sha)
            )
            success = bool(
                status == "completed"
                and run.get("conclusion") == "success"
                and complete
            )
        attempt: dict[str, Any] = {
            "run_id": run_id,
            "active": active,
            "success": success,
            "complete": complete,
        }
        if run.get("updated_at"):
            attempt["updated_at"] = run["updated_at"]
        attempts.append(attempt)
    return sorted(attempts, key=lambda item: int(item["run_id"]))


def latest_successful_attestation_timestamp(
    attempts: list[dict[str, Any]],
) -> str | None:
    return max(
        (
            str(item["updated_at"])
            for item in attempts
            if item.get("success") and item.get("updated_at")
        ),
        default=None,
    )


def changed_paths(pr: dict[str, Any]) -> list[str] | None:
    expected = int(pr.get("changed_files") or 0)
    if expected > MAX_CHANGED_FILES:
        return None
    files = api_list(
        f"repos/{REPO}/pulls/{pr['number']}/files?per_page={MAX_CHANGED_FILES}"
    )
    if len(files) != expected:
        return None
    paths: set[str] = set()
    for item in files:
        if item.get("filename"):
            paths.add(str(item["filename"]))
        if item.get("previous_filename"):
            paths.add(str(item["previous_filename"]))
    return sorted(paths)


def source_and_scope(
    pr: dict[str, Any],
) -> tuple[int | None, dict[str, Any] | None, list[str], str | None]:
    issue_number = parse_issue_number(pr.get("body") or "")
    if not issue_number:
        return None, None, [], "MISSING_TRUSTED_SOURCE_ISSUE"
    issue = api(f"repos/{REPO}/issues/{issue_number}")
    if not trusted_source_issue(issue):
        return issue_number, issue, [], "UNTRUSTED_SOURCE_ISSUE"
    changed = changed_paths(pr)
    if changed is None:
        return issue_number, issue, [], "INCOMPLETE_CHANGED_FILE_EVIDENCE"
    if any(is_protected(path) for path in changed) and not protected_scope_is_authorized(
        changed, issue.get("body") or ""
    ):
        return issue_number, issue, changed, "UNAUTHORIZED_PROTECTED_PATH"
    return issue_number, issue, changed, None


def candidate_pulls() -> list[dict[str, Any]]:
    pulls = api(f"repos/{REPO}/pulls?state=open&per_page=50")
    return [
        pr
        for pr in sorted(pulls, key=lambda item: int(item["number"]))
        if trusted_candidate(pr)
    ][:MAX_CANDIDATES]


def _sanitized_check_evidence(sha: str) -> str:
    payload = api(f"repos/{REPO}/commits/{sha}/check-runs?per_page=100")
    checks = []
    for item in payload.get("check_runs") or []:
        checks.append(
            {
                "name": str(item.get("name") or ""),
                "status": str(item.get("status") or ""),
                "conclusion": str(item.get("conclusion") or ""),
                "app": str(((item.get("app") or {}).get("slug") or "")),
            }
        )
    return json.dumps(
        sorted(checks, key=lambda item: (item["name"], item["app"])),
        sort_keys=True,
        separators=(",", ":"),
    )


def self_resolution_audit(
    pr: dict[str, Any], issue_number: int | None, reason: str
) -> dict[str, str]:
    """Collect real exact-SHA evidence; any failed query prevents record creation."""
    pr_number = _require_positive_number("pr_number", int(pr["number"]))
    sha = _require_exact_sha(str((pr.get("head") or {}).get("sha") or ""))
    if not SAFE_REASON.fullmatch(reason):
        raise ValueError("internal stop reason is not path-safe")

    repository = api(f"repos/{REPO}")
    current_pr = _live_pr(pr_number, sha)
    changed = changed_paths(current_pr)
    attempts = attestation_attempts(sha)
    checks = _sanitized_check_evidence(sha)
    codex = exact_codex_evidence(pr_number, sha)
    unresolved = unresolved_review_threads(pr_number)
    permission = api(
        f"repos/{REPO}/collaborators/{AUTOMATION_OWNER}/permission"
    ).get("permission")
    workflow_states: list[str] = []
    for workflow_name in AUDIT_WORKFLOWS:
        metadata = api(f"repos/{REPO}/actions/workflows/{workflow_name}")
        workflow_states.append(
            f"{workflow_name}:{metadata.get('state', 'unknown')}:{metadata.get('id', 'unknown')}"
        )

    issue_state = "not-applicable"
    authorization_state = "not-applicable"
    if issue_number:
        issue = api(f"repos/{REPO}/issues/{issue_number}")
        issue_state = (
            f"state={issue.get('state', 'unknown')},trusted_author={trusted_source_issue(issue)}"
        )
        authorization_state = (
            "incomplete-path-evidence"
            if changed is None
            else str(protected_scope_is_authorized(changed, issue.get("body") or ""))
        )

    final_pr = _live_pr(pr_number, sha)
    mergeable = final_pr.get("mergeable")
    mergeable_state = str(final_pr.get("mergeable_state") or "unknown")
    if reason == "MERGE_NOT_READY" and mergeable is not False:
        raise RuntimeError("MERGE_NOT_READY is no longer supported by live mergeability")

    return {
        "issue": f"#{issue_number}" if issue_number else "unknown",
        "pull_request": f"#{pr_number}",
        "exact_head_sha": sha,
        "reason_code": reason,
        "repository_metadata": (
            f"visibility={repository.get('visibility', 'unknown')},"
            f"default_branch={repository.get('default_branch', 'unknown')},"
            "initial_and_final_head_confirmed=true"
        ),
        "workflow_run_and_job_evidence": json.dumps(
            attempts, sort_keys=True, separators=(",", ":")
        ),
        "check_evidence": checks,
        "changed_and_renamed_paths": (
            "incomplete" if changed is None else json.dumps(changed, separators=(",", ":"))
        ),
        "scope_and_authorization": (
            f"issue={issue_state},protected_scope={authorization_state}"
        ),
        "review_and_provenance": (
            f"codex={codex['state']},codex_timestamp={codex['timestamp']},"
            f"trusted_request_timestamp={codex['request_timestamp']},"
            f"unresolved_threads={unresolved}"
        ),
        "mergeability": f"mergeable={mergeable},mergeable_state={mergeable_state}",
        "permissions_and_credentials": (
            f"automation_owner_permission={permission or 'unknown'},"
            "secret_values_not_requested=true"
        ),
        "alternative_connected_paths": ";".join(workflow_states),
        "idempotency": internal_stop_record_path(pr_number, sha, reason),
    }


def internal_stop_record_path(pr_number: int, sha: str, reason: str) -> str:
    pr_number = _require_positive_number("pr_number", pr_number)
    sha = _require_exact_sha(sha)
    if not SAFE_REASON.fullmatch(reason):
        raise ValueError("internal stop reason is not path-safe")
    return f"{INTERNAL_STOP_ROOT}/pr-{pr_number}/{sha}/{reason}.json"


def canonical_internal_stop_record(
    *,
    pr_number: int,
    issue_number: int | None,
    sha: str,
    reason: str,
    detail: str,
    audit: dict[str, str],
) -> str:
    record = {
        "schema_version": 1,
        "notification": False,
        "required_human_action": None,
        "reason_code": reason,
        "issue_number": issue_number,
        "pull_request_number": pr_number,
        "exact_head_sha": sha,
        "detail": detail,
        "audit": audit,
    }
    return json.dumps(record, sort_keys=True, indent=2, ensure_ascii=True) + "\n"


def _not_found(result: subprocess.CompletedProcess[str]) -> bool:
    combined = f"{result.stdout}\n{result.stderr}".lower()
    return result.returncode != 0 and ("http 404" in combined or "not found" in combined)


def ensure_internal_stop_branch() -> None:
    ref_path = f"repos/{REPO}/git/ref/heads/{INTERNAL_STOP_BRANCH}"
    current = gh_result("api", "-H", "Accept: application/vnd.github+json", ref_path)
    if current.returncode == 0:
        return
    if not _not_found(current):
        raise RuntimeError(f"Could not inspect internal-stop branch: {current.stderr.strip()}")
    default_sha = current_default_sha()
    created = gh_result(
        "api",
        "--method",
        "POST",
        f"repos/{REPO}/git/refs",
        "-f",
        f"ref=refs/heads/{INTERNAL_STOP_BRANCH}",
        "-f",
        f"sha={default_sha}",
    )
    if created.returncode == 0:
        return
    raced = gh_result("api", "-H", "Accept: application/vnd.github+json", ref_path)
    if raced.returncode != 0:
        raise RuntimeError(f"Could not create internal-stop branch: {created.stderr.strip()}")


def _existing_internal_record(path: str) -> str | None:
    result = gh_result(
        "api",
        "-H",
        "Accept: application/vnd.github+json",
        f"repos/{REPO}/contents/{path}?ref={INTERNAL_STOP_BRANCH}",
    )
    if result.returncode == 0:
        payload = json.loads(result.stdout)
        encoded = str(payload.get("content") or "").replace("\n", "")
        return base64.b64decode(encoded).decode("utf-8")
    if _not_found(result):
        return None
    raise RuntimeError(f"Could not inspect internal stop record: {result.stderr.strip()}")


def persist_internal_stop_record(path: str, content: str, reason: str, pr_number: int) -> bool:
    """Create one deterministic record commit; an existing path is the idempotency key."""
    ensure_internal_stop_branch()
    existing = _existing_internal_record(path)
    if existing is not None:
        payload = json.loads(existing)
        if (
            payload.get("reason_code") != reason
            or int(payload.get("pull_request_number") or 0) != pr_number
        ):
            raise RuntimeError("Existing internal stop record does not match its deterministic path")
        return False
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    created = gh_result(
        "api",
        "--method",
        "PUT",
        f"repos/{REPO}/contents/{path}",
        "-f",
        f"message=Record {reason} for PR #{pr_number}",
        "-f",
        f"content={encoded}",
        "-f",
        f"branch={INTERNAL_STOP_BRANCH}",
    )
    if created.returncode == 0:
        return True
    raced = _existing_internal_record(path)
    if raced is not None:
        return False
    raise RuntimeError(f"Could not persist internal stop record: {created.stderr.strip()}")


def stop_report(
    pr: dict[str, Any],
    issue_number: int | None,
    reason: str,
    detail: str,
    close: bool = False,
) -> None:
    """Persist one non-comment internal record on the fixed branch."""
    if reason in HUMAN_ONLY_REASONS:
        raise ValueError("human-only reasons must use the audited human-only formatter")
    sha = _require_exact_sha(str(pr["head"]["sha"]))
    pr_number = int(pr["number"])
    audit = self_resolution_audit(pr, issue_number, reason)
    live = _live_pr(pr_number, sha)
    if reason == "MERGE_NOT_READY" and live.get("mergeable") is not False:
        raise RuntimeError("terminal mergeability changed before stop persistence")
    path = internal_stop_record_path(pr_number, sha, reason)
    content = canonical_internal_stop_record(
        pr_number=pr_number,
        issue_number=issue_number,
        sha=sha,
        reason=reason,
        detail=detail,
        audit=audit,
    )
    _live_pr(pr_number, sha)
    persist_internal_stop_record(path, content, reason, pr_number)
    ensure_label(
        pr_number,
        "ai-blocked",
        "B60205",
        "Automation stopped after self-resolution",
    )
    if close:
        _live_pr(pr_number, sha)
        gh("pr", "close", str(pr_number), "--repo", REPO)


def format_human_only_notice(
    *,
    reason: str,
    issue_number: int,
    pr_number: int,
    exact_head_sha: str,
    attempted_connected_paths: Iterable[str],
    impossibility_evidence: Iterable[str],
    provider_ui_action: str,
    automatic_resume_condition: str,
    targets: Iterable[str],
) -> str:
    if reason not in HUMAN_ONLY_REASONS:
        raise ValueError("reason is not an allowed human-only notice family")
    issue_number = _require_positive_number("issue_number", issue_number)
    pr_number = _require_positive_number("pr_number", pr_number)
    exact_head_sha = _require_exact_sha(exact_head_sha)
    attempted = _normalized_evidence(
        attempted_connected_paths, "attempted_connected_paths"
    )
    evidence = _normalized_evidence(impossibility_evidence, "impossibility_evidence")
    target_list = _normalized_evidence(targets, "targets")
    expected_action = HUMAN_ONLY_ACTIONS[reason]
    if provider_ui_action.strip() != expected_action:
        raise ValueError("provider_ui_action is not the canonical reason-compatible action")
    if not automatic_resume_condition.strip():
        raise ValueError("automatic_resume_condition is required")
    if reason == "HUMAN_ONLY_ACCOUNT_LEVEL_REPOSITORY_CREATION_UI_UNAVAILABLE":
        if len(target_list) != 2 or any("/" not in item for item in target_list):
            raise ValueError(
                "repository-creation notice requires exactly two owner/name targets"
            )
    marker = (
        f"{HUMAN_NOTICE_PREFIX}{reason}:{exact_head_sha}:"
        f"{issue_number}:{pr_number} -->"
    )
    attempted_text = "\n".join(f"  - `{item}`" for item in attempted)
    evidence_text = "\n".join(f"  - {item}" for item in evidence)
    targets_text = "\n".join(f"  - `{item}`" for item in target_list)
    return (
        f"{marker}\n## Audited human-only action required\n\n"
        "- notification: `true`\n"
        f"- reason_code: `{reason}`\n"
        f"- issue: `#{issue_number}`\n"
        f"- pull_request: `#{pr_number}`\n"
        f"- exact_head_sha: `{exact_head_sha}`\n"
        "- targets:\n"
        f"{targets_text}\n"
        "- attempted_connected_paths:\n"
        f"{attempted_text}\n"
        "- impossibility_evidence:\n"
        f"{evidence_text}\n"
        f"- required_provider_ui_action: {expected_action}\n"
        f"- automatic_resume_condition: {automatic_resume_condition.strip()}\n"
    )


def _validated_notice_destination(
    pr_number: int, issue_number: int, exact_head_sha: str
) -> dict[str, Any]:
    live = _live_pr(pr_number, exact_head_sha)
    if live.get("state") != "open":
        raise RuntimeError("human-only notice destination is not an open Pull Request")
    if parse_issue_number(live.get("body") or "") != issue_number:
        raise RuntimeError("human-only notice Issue linkage does not match the live Pull Request")
    issue = api(f"repos/{REPO}/issues/{issue_number}")
    if not trusted_source_issue(issue):
        raise RuntimeError("human-only notice source Issue is not trusted")
    if not trusted_candidate(live):
        raise RuntimeError("human-only notice destination is not a trusted same-repository candidate")
    return live


def human_only_notice(
    *,
    reason: str,
    issue_number: int,
    pr_number: int,
    exact_head_sha: str,
    attempted_connected_paths: Iterable[str],
    impossibility_evidence: Iterable[str],
    provider_ui_action: str,
    automatic_resume_condition: str,
    targets: Iterable[str],
) -> None:
    """Perform the mandatory audit, validate live state, and post one immutable bot comment."""
    body = format_human_only_notice(
        reason=reason,
        issue_number=issue_number,
        pr_number=pr_number,
        exact_head_sha=exact_head_sha,
        attempted_connected_paths=attempted_connected_paths,
        impossibility_evidence=impossibility_evidence,
        provider_ui_action=provider_ui_action,
        automatic_resume_condition=automatic_resume_condition,
        targets=targets,
    )
    live = _validated_notice_destination(pr_number, issue_number, exact_head_sha)
    audit = self_resolution_audit(live, issue_number, reason)
    body += "- self_resolution_audit:\n" + "\n".join(
        f"  - {key}: `{value}`" for key, value in audit.items()
    ) + "\n"
    marker = body.splitlines()[0]
    expected_suffix = f":{issue_number}:{pr_number} -->"
    if not marker.startswith(HUMAN_NOTICE_PREFIX) or not marker.endswith(expected_suffix):
        raise ValueError("validated notice marker is not bound to the destination")
    comments = api_list(f"repos/{REPO}/issues/{pr_number}/comments?per_page=100")
    if any(
        (item.get("user") or {}).get("login") == ACTIONS_LOGIN
        and item.get("created_at") == item.get("updated_at")
        and marker in (item.get("body") or "")
        for item in comments
    ):
        return
    _validated_notice_destination(pr_number, issue_number, exact_head_sha)
    comment(pr_number, body)


def discover_targets() -> list[str]:
    targets: list[str] = []
    for observed in candidate_pulls():
        pr = api(f"repos/{REPO}/pulls/{observed['number']}")
        if pr["head"]["sha"] != observed["head"]["sha"] or not trusted_candidate(pr):
            continue
        _, _, _, scope_error = source_and_scope(pr)
        if scope_error:
            continue
        attempts = attestation_attempts(str(pr["head"]["sha"]))
        if any(item["success"] or item["active"] for item in attempts):
            continue
        if len({item["run_id"] for item in attempts}) >= MAX_ATTESTATION_ATTEMPTS:
            continue
        targets.append(str(pr["head"]["sha"]))
    return targets


def request_codex(pr_number: int, sha: str) -> None:
    marker = f"<!-- foundation-codex-request:{sha} -->"
    comments = api_list(f"repos/{REPO}/issues/{pr_number}/comments?per_page=100")
    if any(
        (item.get("user") or {}).get("login") == ACTIONS_LOGIN
        and marker in (item.get("body") or "")
        and item.get("created_at") == item.get("updated_at")
        for item in comments
    ):
        return
    comment(
        pr_number,
        f"{marker}\n@codex review\n\nReview exact head `{sha}`. Report blocking findings only.",
    )


def _evidence_anchor(*values: str | None) -> str | None:
    parsed = [(value, _parse_timestamp(value)) for value in values if value]
    parsed = [(value, timestamp) for value, timestamp in parsed if timestamp is not None]
    if not parsed:
        return None
    return max(parsed, key=lambda item: item[1])[0]


def supervise() -> None:
    for observed in candidate_pulls():
        pr = api(f"repos/{REPO}/pulls/{observed['number']}")
        sha = str(pr["head"]["sha"])
        if sha != observed["head"]["sha"] or not trusted_candidate(pr):
            continue

        issue_number, issue, _, scope_error = source_and_scope(pr)
        if scope_error == "MISSING_TRUSTED_SOURCE_ISSUE":
            stop_report(pr, None, scope_error, "PR body identifies no trusted source Issue.")
            continue
        if scope_error == "UNTRUSTED_SOURCE_ISSUE":
            stop_report(
                pr,
                issue_number,
                scope_error,
                "The referenced source is not a trusted owner-authored repository Issue.",
            )
            continue
        if scope_error == "INCOMPLETE_CHANGED_FILE_EVIDENCE":
            stop_report(
                pr,
                issue_number,
                scope_error,
                f"Changed/renamed path evidence exceeded or did not match the bounded {MAX_CHANGED_FILES}-file snapshot.",
            )
            continue
        if scope_error == "UNAUTHORIZED_PROTECTED_PATH":
            issue_body = (issue or {}).get("body") or ""
            auto_close = bool(
                E2E_AUTO_CLOSE_MARKER in issue_body
                or any(
                    label.get("name") == "e2e-auto-close"
                    for label in pr.get("labels") or []
                )
            )
            stop_report(
                pr,
                issue_number,
                scope_error,
                "Protected changed or renamed paths lack exact Issue authorization.",
                close=auto_close,
            )
            continue

        attempts = attestation_attempts(sha)
        if not any(item["success"] for item in attempts):
            if (
                not any(item["active"] for item in attempts)
                and len({item["run_id"] for item in attempts})
                >= MAX_ATTESTATION_ATTEMPTS
            ):
                stop_report(
                    pr,
                    issue_number,
                    "TRUSTED_ATTESTATION_RETRY_EXHAUSTED",
                    "Three fixed candidate-bound workflow attempts completed without one complete successful immutable run/job evidence set.",
                )
            continue

        codex = exact_codex_evidence(int(pr["number"]), sha)
        if codex["state"] == "pending":
            request_codex(int(pr["number"]), sha)
            elapsed = minutes_since(codex.get("request_timestamp"))
            if elapsed is not None and elapsed >= NO_PROGRESS_MINUTES:
                stop_report(
                    pr,
                    issue_number,
                    "NO_MEANINGFUL_PROGRESS",
                    "No exact-SHA Codex evidence changed within the bounded interval measured from the immutable trusted request comment.",
                )
            continue
        if codex["state"] == "blocking":
            stop_report(
                pr,
                issue_number,
                "BLOCKING_CODEX_REVIEW",
                "Exact-head Codex evidence contains a blocking finding or unresolved review thread.",
            )
            continue

        current = api(f"repos/{REPO}/pulls/{pr['number']}")
        if current["head"]["sha"] != sha or not trusted_candidate(current):
            continue
        remove_label(int(pr["number"]), "ai-blocked")
        if current.get("draft"):
            gh("pr", "ready", str(pr["number"]), "--repo", REPO)
            current = api(f"repos/{REPO}/pulls/{pr['number']}")
            if current["head"]["sha"] != sha or not trusted_candidate(current):
                continue
        if current.get("mergeable") is False:
            stop_report(
                current,
                issue_number,
                "MERGE_NOT_READY",
                f"GitHub reports mergeable=false with state {current.get('mergeable_state', 'unknown')}.",
            )
            continue
        if current.get("mergeable") is not True:
            anchor = _evidence_anchor(
                latest_successful_attestation_timestamp(attempts),
                str(codex.get("timestamp") or "") or None,
            )
            elapsed = minutes_since(anchor)
            if elapsed is not None and elapsed >= NO_PROGRESS_MINUTES:
                stop_report(
                    current,
                    issue_number,
                    "NO_MEANINGFUL_PROGRESS",
                    "Mergeability remained indeterminate for the bounded interval measured from the latest immutable clean evidence.",
                )
            continue
        gh(
            "api",
            "--method",
            "PUT",
            f"repos/{REPO}/pulls/{pr['number']}/merge",
            "-f",
            "merge_method=squash",
            "-f",
            f"sha={sha}",
        )


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "discover":
        print(json.dumps(discover_targets(), separators=(",", ":")))
        return 0
    supervise()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

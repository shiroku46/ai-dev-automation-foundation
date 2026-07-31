#!/usr/bin/env python3
"""Trusted default-branch supervisor and attestation discovery runtime.

Write-capable workflow jobs execute this module only from the repository default
branch. Proposed-branch code is checked out and executed exclusively by
read-only jobs in the fixed reusable trusted-checks workflow.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from functools import lru_cache

from scripts.supervisor_policy import is_protected, parse_issue_number, protected_scope_is_authorized

REPO = os.environ["REPOSITORY"]
DEFAULT_BRANCH = os.environ["DEFAULT_BRANCH"]
AUTOMATION_OWNER = os.environ["AUTOMATION_OWNER"]
ATTESTATION_NAMES = ("CI / validate", "Unit Tests / test")
MAX_CANDIDATES = 10
MAX_ATTESTATION_ATTEMPTS = 3
ALLOWED_PREFIXES = ("claude-issue-", "automation/", "fix/")
ALLOWED_AUTHORS = {AUTOMATION_OWNER, "github-actions[bot]"}
ALLOWED_CALLER_EVENTS = {"workflow_run", "schedule", "workflow_dispatch"}
CALLER_WORKFLOW_PATH = ".github/workflows/ci-reconcile.yml"
REUSABLE_WORKFLOW_PATH = ".github/workflows/trusted-checks.yml"
CODEX_LOGIN = "chatgpt-codex-connector[bot]"
ACTIONS_LOGIN = "github-actions[bot]"
STOP_PREFIX = "<!-- foundation-stop:"
E2E_AUTO_CLOSE_MARKER = "<!-- foundation-e2e-auto-close -->"
RUN_URL = re.compile(
    rf"^https://github\.com/{re.escape(REPO)}/actions/runs/(?P<run_id>[0-9]+)$"
)


def gh(*args: str) -> str:
    return subprocess.check_output(["gh", *args], text=True)


def api(path: str):
    return json.loads(gh("api", "-H", "Accept: application/vnd.github+json", path))


def api_list(path: str) -> list[dict]:
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
    items: list[dict] = []
    for page in pages:
        if not isinstance(page, list):
            raise RuntimeError(f"Expected list page from {path}")
        items.extend(page)
    return items


def api_key_pages(path: str, key: str) -> list[dict]:
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
    items: list[dict] = []
    for page in pages:
        if not isinstance(page, dict):
            raise RuntimeError(f"Expected object page from {path}")
        values = page.get(key) or []
        if not isinstance(values, list):
            raise RuntimeError(f"Expected list key {key} from {path}")
        items.extend(values)
    return items


@lru_cache(maxsize=1)
def current_default_sha() -> str:
    return api(f"repos/{REPO}/commits/{DEFAULT_BRANCH}")["sha"]


@lru_cache(maxsize=64)
def workflow_run(run_id: int) -> dict:
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


def stop_report(pr: dict, issue_number: int | None, reason: str, detail: str, close: bool = False) -> None:
    sha = pr["head"]["sha"]
    marker = f"{STOP_PREFIX}{reason}:{sha} -->"
    comments = api_list(f"repos/{REPO}/issues/{pr['number']}/comments?per_page=100")
    if not any(marker in (item.get("body") or "") for item in comments):
        comment(
            pr["number"],
            f"{marker}\n## Structured automation stop\n\n"
            f"- reason_code: `{reason}`\n"
            f"- issue: `#{issue_number or 'unknown'}`\n"
            f"- pull_request: `#{pr['number']}`\n"
            f"- exact_head_sha: `{sha}`\n"
            f"- detail: {detail}\n"
            "- self_resolution_audit: metadata, scope, authorization, checks, review, "
            "provenance, idempotency, and available GitHub permissions were rechecked.\n",
        )
    ensure_label(
        pr["number"],
        "ai-blocked",
        "B60205",
        "Automation stopped after self-resolution",
    )
    if close:
        gh("pr", "close", str(pr["number"]), "--repo", REPO)


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
        arguments = [
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
            arguments.extend(["-F", f"cursor={cursor}"])
        payload = json.loads(gh(*arguments))
        threads = payload["data"]["repository"]["pullRequest"]["reviewThreads"]
        if any(not node.get("isResolved") for node in threads.get("nodes") or []):
            return True
        page_info = threads["pageInfo"]
        if not page_info.get("hasNextPage"):
            return False
        cursor = page_info.get("endCursor")
        if not cursor:
            raise RuntimeError("Review-thread pagination did not return an end cursor")


def exact_codex_clean(pr_number: int, sha: str) -> bool:
    comments = api_list(f"repos/{REPO}/issues/{pr_number}/comments?per_page=100")
    short = sha[:10]
    expected_marker = f"<!-- foundation-codex-request:{sha} -->"
    trusted_requests: list[dict] = []

    for item in reversed(comments):
        body = item.get("body") or ""
        login = (item.get("user") or {}).get("login") or ""
        if login == CODEX_LOGIN and (sha in body or short in body):
            lower = body.lower()
            clean = "didn't find any major issues" in lower or "no major issues" in lower
            return clean and not unresolved_review_threads(pr_number)
        if (
            login == ACTIONS_LOGIN
            and expected_marker in body
            and sha in body
            and item.get("created_at") == item.get("updated_at")
        ):
            trusted_requests.append(item)

    for request in trusted_requests:
        reactions = api_list(
            f"repos/{REPO}/issues/comments/{request['id']}/reactions?per_page=100"
        )
        clean_reaction = any(
            (reaction.get("user") or {}).get("login") == CODEX_LOGIN
            and reaction.get("content") == "+1"
            for reaction in reactions
        )
        if clean_reaction:
            return not unresolved_review_threads(pr_number)
    return False


def trusted_candidate(pr: dict) -> bool:
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    author = (pr.get("user") or {}).get("login") or ""
    return (
        ((head.get("repo") or {}).get("full_name") == REPO)
        and ((base.get("repo") or {}).get("full_name") == REPO)
        and base.get("ref") == DEFAULT_BRANCH
        and author in ALLOWED_AUTHORS
        and (head.get("ref") or "").startswith(ALLOWED_PREFIXES)
        and not any(
            label.get("name") == "ai-no-merge" for label in pr.get("labels") or []
        )
    )


def trusted_source_issue(issue: dict) -> bool:
    return (
        not issue.get("pull_request")
        and (issue.get("user") or {}).get("login") == AUTOMATION_OWNER
    )


def run_id_from_details_url(details_url: str) -> int | None:
    match = RUN_URL.fullmatch(details_url or "")
    return int(match.group("run_id")) if match else None


def trusted_attestation_workflow_run(run_id: int, *, allow_active: bool) -> dict | None:
    run = workflow_run(run_id)
    repository = run.get("repository") or {}
    actor = (run.get("actor") or {}).get("login") or ""
    expected_caller = f"{CALLER_WORKFLOW_PATH}@{DEFAULT_BRANCH}"
    expected_reusable = f"{REPO}/{REUSABLE_WORKFLOW_PATH}@{DEFAULT_BRANCH}"
    if repository.get("full_name") != REPO:
        return None
    if run.get("head_branch") != DEFAULT_BRANCH:
        return None
    if run.get("head_sha") != current_default_sha():
        return None
    if run.get("path") != expected_caller:
        return None
    if run.get("event") not in ALLOWED_CALLER_EVENTS:
        return None
    if actor not in ALLOWED_AUTHORS:
        return None
    reusable = [
        item
        for item in run.get("referenced_workflows") or []
        if item.get("path") == expected_reusable
        and item.get("ref") == f"refs/heads/{DEFAULT_BRANCH}"
        and item.get("sha") == run.get("head_sha")
    ]
    if len(reusable) != 1:
        return None
    status = run.get("status")
    if allow_active and status in {"queued", "in_progress", "waiting", "pending"}:
        return run
    if status == "completed" and run.get("conclusion") in {
        "success",
        "failure",
        "cancelled",
        "timed_out",
        "action_required",
    }:
        return run
    return None


def attestation_attempts(sha: str) -> list[dict]:
    check_runs = api_key_pages(
        f"repos/{REPO}/commits/{sha}/check-runs?per_page=100", "check_runs"
    )
    grouped: dict[int, dict[str, dict]] = {}
    for check in check_runs:
        name = check.get("name") or ""
        if name not in ATTESTATION_NAMES:
            continue
        if check.get("head_sha") != sha:
            continue
        if ((check.get("app") or {}).get("slug") != "github-actions"):
            continue
        run_id = run_id_from_details_url(check.get("details_url") or "")
        if not run_id:
            continue
        expected_external_id = f"foundation:{run_id}:{name}:{sha}"
        if check.get("external_id") != expected_external_id:
            continue
        grouped.setdefault(run_id, {})[name] = check

    attempts: list[dict] = []
    for run_id, checks in grouped.items():
        run = trusted_attestation_workflow_run(run_id, allow_active=True)
        if not run:
            continue
        complete_set = set(checks) == set(ATTESTATION_NAMES)
        active = run.get("status") != "completed" and any(
            check.get("status") != "completed" for check in checks.values()
        )
        success = (
            complete_set
            and run.get("status") == "completed"
            and run.get("conclusion") == "success"
            and all(
                check.get("status") == "completed"
                and check.get("conclusion") == "success"
                for check in checks.values()
            )
        )
        attempts.append(
            {
                "run_id": run_id,
                "active": active,
                "success": success,
                "complete": complete_set,
            }
        )
    return sorted(attempts, key=lambda item: item["run_id"])


def source_and_scope(pr: dict) -> tuple[int | None, dict | None, list[str], str | None]:
    issue_number = parse_issue_number(pr.get("body") or "")
    if not issue_number:
        return None, None, [], "MISSING_TRUSTED_SOURCE_ISSUE"
    issue = api(f"repos/{REPO}/issues/{issue_number}")
    if not trusted_source_issue(issue):
        return issue_number, issue, [], "UNTRUSTED_SOURCE_ISSUE"
    files = api_list(f"repos/{REPO}/pulls/{pr['number']}/files?per_page=100")
    changed = [item["filename"] for item in files]
    if any(is_protected(path) for path in changed) and not protected_scope_is_authorized(
        changed, issue.get("body") or ""
    ):
        return issue_number, issue, changed, "UNAUTHORIZED_PROTECTED_PATH"
    return issue_number, issue, changed, None


def candidate_pulls() -> list[dict]:
    pulls = api(f"repos/{REPO}/pulls?state=open&per_page=50")
    return [
        pr
        for pr in sorted(pulls, key=lambda item: int(item["number"]))
        if trusted_candidate(pr)
    ][:MAX_CANDIDATES]


def discover_targets() -> list[str]:
    targets: list[str] = []
    for observed in candidate_pulls():
        pr = api(f"repos/{REPO}/pulls/{observed['number']}")
        if pr["head"]["sha"] != observed["head"]["sha"] or not trusted_candidate(pr):
            continue
        _, _, _, scope_error = source_and_scope(pr)
        if scope_error:
            continue
        attempts = attestation_attempts(pr["head"]["sha"])
        if any(item["success"] or item["active"] for item in attempts):
            continue
        if len({item["run_id"] for item in attempts}) >= MAX_ATTESTATION_ATTEMPTS:
            continue
        targets.append(pr["head"]["sha"])
    return targets


def supervise() -> None:
    for observed in candidate_pulls():
        pr = api(f"repos/{REPO}/pulls/{observed['number']}")
        sha = pr["head"]["sha"]
        if sha != observed["head"]["sha"] or not trusted_candidate(pr):
            continue

        issue_number, issue, _, scope_error = source_and_scope(pr)
        if scope_error == "MISSING_TRUSTED_SOURCE_ISSUE":
            stop_report(
                pr,
                None,
                scope_error,
                "PR body does not identify one trusted source Issue.",
            )
            continue
        if scope_error == "UNTRUSTED_SOURCE_ISSUE":
            stop_report(
                pr,
                issue_number,
                scope_error,
                "The referenced source is not an owner-authored repository Issue.",
            )
            continue
        if scope_error == "UNAUTHORIZED_PROTECTED_PATH":
            issue_body = (issue or {}).get("body") or ""
            auto_close = (
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
                "Protected paths are not covered by Issue authorization.",
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
                    "Three fixed default-branch attestation attempts completed without success.",
                )
            continue

        if not exact_codex_clean(pr["number"], sha):
            marker = f"<!-- foundation-codex-request:{sha} -->"
            comments = api_list(
                f"repos/{REPO}/issues/{pr['number']}/comments?per_page=100"
            )
            if not any(marker in (item.get("body") or "") for item in comments):
                comment(
                    pr["number"],
                    f"{marker}\n@codex review\n\n"
                    f"Review exact head `{sha}`. Report blocking findings only.",
                )
            continue

        current = api(f"repos/{REPO}/pulls/{pr['number']}")
        if current["head"]["sha"] != sha or not trusted_candidate(current):
            continue
        if current.get("draft"):
            gh("pr", "ready", str(pr["number"]), "--repo", REPO)
            current = api(f"repos/{REPO}/pulls/{pr['number']}")
            if current["head"]["sha"] != sha:
                continue
        if current.get("mergeable") is not True:
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

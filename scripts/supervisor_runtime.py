#!/usr/bin/env python3
"""Trusted default-branch supervisor; proposed-branch code is never executed here."""
from __future__ import annotations

import json
import os
import subprocess

from scripts.supervisor_policy import is_protected, parse_issue_number, protected_scope_is_authorized

REPO = os.environ["REPOSITORY"]
AUTOMATION_OWNER = os.environ["AUTOMATION_OWNER"]
REQUIRED_JOBS = {"validate": "CI / validate", "test": "Unit Tests / test"}
MAX_CANDIDATES = 10
ALLOWED_PREFIXES = ("claude-issue-", "automation/", "fix/")
ALLOWED_AUTHORS = {AUTOMATION_OWNER, "github-actions[bot]"}
CODEX_LOGIN = "chatgpt-codex-connector[bot]"
STOP_PREFIX = "<!-- foundation-stop:"


def gh(*args: str) -> str:
    return subprocess.check_output(["gh", *args], text=True)


def api(path: str):
    return json.loads(gh("api", "-H", "Accept: application/vnd.github+json", path))


def comment(number: int, body: str) -> None:
    gh("issue", "comment", str(number), "--repo", REPO, "--body", body)


def ensure_label(number: int, label: str, color: str, description: str) -> None:
    gh("label", "create", label, "--repo", REPO, "--color", color,
       "--description", description, "--force")
    gh("issue", "edit", str(number), "--repo", REPO, "--add-label", label)


def stop_report(pr, issue_number, reason, detail, close=False) -> None:
    sha = pr["head"]["sha"]
    marker = f"{STOP_PREFIX}{reason}:{sha} -->"
    comments = api(f"repos/{REPO}/issues/{pr['number']}/comments?per_page=100")
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
    ensure_label(pr["number"], "ai-blocked", "B60205",
                 "Automation stopped after self-resolution")
    if close:
        gh("pr", "close", str(pr["number"]), "--repo", REPO)


def unresolved_review_threads(pr_number: int) -> bool:
    owner, name = REPO.split("/", 1)
    query = """
    query($owner:String!,$name:String!,$number:Int!){
      repository(owner:$owner,name:$name){
        pullRequest(number:$number){
          reviewThreads(first:100){nodes{isResolved}}
        }
      }
    }
    """
    payload = json.loads(gh("api", "graphql", "-f", f"query={query}",
                            "-F", f"owner={owner}", "-F", f"name={name}",
                            "-F", f"number={pr_number}"))
    nodes = payload["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    return any(not node.get("isResolved") for node in nodes)


def exact_codex_clean(pr_number: int, sha: str) -> bool:
    comments = api(f"repos/{REPO}/issues/{pr_number}/comments?per_page=100")
    short = sha[:10]
    relevant_requests = []
    for item in reversed(comments):
        body = item.get("body") or ""
        login = (item.get("user") or {}).get("login") or ""
        if short not in body and sha not in body:
            continue
        if login == CODEX_LOGIN:
            lower = body.lower()
            return (
                "didn't find any major issues" in lower
                or "no major issues" in lower
            ) and not unresolved_review_threads(pr_number)
        if "@codex review" in body:
            relevant_requests.append(item)

    for request in relevant_requests:
        reactions = api(
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


def trusted_checks_for(sha: str) -> dict[str, str]:
    runs = api(f"repos/{REPO}/commits/{sha}/check-runs?per_page=100").get(
        "check_runs", []
    )
    result: dict[str, str] = {}
    for run in sorted(runs, key=lambda item: int(item.get("id") or 0), reverse=True):
        if ((run.get("app") or {}).get("slug") != "github-actions"):
            continue
        context = REQUIRED_JOBS.get(run.get("name") or "")
        if not context or context in result:
            continue
        result[context] = (
            run.get("conclusion") or "failure"
            if run.get("status") == "completed"
            else run.get("status") or "queued"
        )
    return result


def trusted_candidate(pr) -> bool:
    head = pr.get("head") or {}
    author = (pr.get("user") or {}).get("login") or ""
    return (
        ((head.get("repo") or {}).get("full_name") == REPO)
        and author in ALLOWED_AUTHORS
        and (head.get("ref") or "").startswith(ALLOWED_PREFIXES)
        and not any(label.get("name") == "ai-no-merge"
                    for label in pr.get("labels") or [])
    )


def main() -> None:
    pulls = api(f"repos/{REPO}/pulls?state=open&per_page=50")
    candidates = [pr for pr in sorted(pulls, key=lambda item: int(item["number"]))
                  if trusted_candidate(pr)][:MAX_CANDIDATES]

    for observed in candidates:
        pr = api(f"repos/{REPO}/pulls/{observed['number']}")
        sha = pr["head"]["sha"]
        if sha != observed["head"]["sha"] or not trusted_candidate(pr):
            continue
        issue_number = parse_issue_number(pr.get("body") or "")
        if not issue_number:
            stop_report(pr, None, "MISSING_TRUSTED_SOURCE_ISSUE",
                        "PR body does not identify one trusted source Issue.")
            continue

        issue = api(f"repos/{REPO}/issues/{issue_number}")
        files = api(f"repos/{REPO}/pulls/{pr['number']}/files?per_page=100")
        changed = [item["filename"] for item in files]
        protected = [path for path in changed if is_protected(path)]
        if protected and not protected_scope_is_authorized(changed, issue.get("body") or ""):
            auto_close = any(label.get("name") == "e2e-auto-close"
                             for label in pr.get("labels") or [])
            stop_report(pr, issue_number, "UNAUTHORIZED_PROTECTED_PATH",
                        "Protected paths are not covered by Issue authorization.",
                        close=auto_close)
            continue

        states = trusted_checks_for(sha)
        required = set(REQUIRED_JOBS.values())
        if set(states) != required or any(states[name] != "success" for name in required):
            continue
        if not exact_codex_clean(pr["number"], sha):
            marker = f"<!-- foundation-codex-request:{sha} -->"
            comments = api(f"repos/{REPO}/issues/{pr['number']}/comments?per_page=100")
            if not any(marker in (item.get("body") or "") for item in comments):
                comment(pr["number"], f"{marker}\n@codex review\n\n"
                        f"Review exact head `{sha}`. Report blocking findings only.")
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
        gh("api", "--method", "PUT", f"repos/{REPO}/pulls/{pr['number']}/merge",
           "-f", "merge_method=squash", "-f", f"sha={sha}")


if __name__ == "__main__":
    main()

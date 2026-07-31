#!/usr/bin/env python3
"""Trusted default-branch GitHub supervisor runtime.

This runtime inspects GitHub metadata only. It never checks out or executes
proposed-branch code.
"""
from __future__ import annotations

import json
import os
import subprocess

from scripts.supervisor_policy import (
    is_protected,
    parse_issue_number,
    protected_scope_is_authorized,
)

REPO = os.environ["REPOSITORY"]
DEFAULT_BRANCH = os.environ["DEFAULT_BRANCH"]
AUTOMATION_OWNER = os.environ["AUTOMATION_OWNER"]
REQUIRED_JOBS = {
    "validate": "CI / validate",
    "test": "Unit Tests / test",
}
MAX_CANDIDATES = 10
ALLOWED_PREFIXES = ("claude-issue-", "automation/", "fix/")
ALLOWED_AUTHORS = {AUTOMATION_OWNER, "github-actions[bot]"}
STOP_PREFIX = "<!-- foundation-stop:"
CODEX_LOGIN = "chatgpt-codex-connector[bot]"


def gh(*args: str) -> str:
    return subprocess.check_output(["gh", *args], text=True)


def api(path: str):
    return json.loads(gh("api", "-H", "Accept: application/vnd.github+json", path))


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


def stop_report(pr, issue_number, reason, detail, close=False):
    sha = pr["head"]["sha"]
    marker = f"{STOP_PREFIX}{reason}:{sha} -->"
    comments = api(f"repos/{REPO}/issues/{pr['number']}/comments?per_page=100")
    if not any(marker in (item.get("body") or "") for item in comments):
        body = (
            f"{marker}\n"
            "## Structured automation stop\n\n"
            f"- reason_code: `{reason}`\n"
            f"- issue: `#{issue_number or 'unknown'}`\n"
            f"- pull_request: `#{pr['number']}`\n"
            f"- exact_head_sha: `{sha}`\n"
            f"- detail: {detail}\n"
            "- self_resolution_audit: metadata, scope, authorization, checks, review, "
            "provenance, idempotency, and available GitHub permissions were rechecked.\n"
        )
        comment(pr["number"], body)
    ensure_label(
        pr["number"],
        "ai-blocked",
        "B60205",
        "Automation stopped after self-resolution",
    )
    if close:
        gh("pr", "close", str(pr["number"]), "--repo", REPO)


def exact_codex_clean(pr_number: int, sha: str) -> bool:
    comments = api(f"repos/{REPO}/issues/{pr_number}/comments?per_page=100")
    short = sha[:10]
    for item in reversed(comments):
        if (item.get("user") or {}).get("login") != CODEX_LOGIN:
            continue
        body = item.get("body") or ""
        if short not in body and sha not in body:
            continue
        lower = body.lower()
        return "didn't find any major issues" in lower or "no major issues" in lower
    return False


def trusted_checks_for(sha: str) -> dict[str, str]:
    check_runs = api(f"repos/{REPO}/commits/{sha}/check-runs?per_page=100").get(
        "check_runs", []
    )
    result: dict[str, str] = {}
    for check in sorted(
        check_runs,
        key=lambda item: int(item.get("id") or 0),
        reverse=True,
    ):
        if ((check.get("app") or {}).get("slug") != "github-actions"):
            continue
        job_name = check.get("name") or ""
        context = REQUIRED_JOBS.get(job_name)
        if not context or context in result:
            continue
        if check.get("status") == "completed":
            result[context] = check.get("conclusion") or "failure"
        else:
            result[context] = check.get("status") or "queued"
    return result


def trusted_candidate(pr) -> bool:
    head = pr.get("head") or {}
    author = (pr.get("user") or {}).get("login") or ""
    if ((head.get("repo") or {}).get("full_name") != REPO):
        return False
    if author not in ALLOWED_AUTHORS:
        return False
    if not (head.get("ref") or "").startswith(ALLOWED_PREFIXES):
        return False
    if any(label.get("name") == "ai-no-merge" for label in pr.get("labels") or []):
        return False
    return True


def main():
    pulls = api(f"repos/{REPO}/pulls?state=open&per_page=50")
    candidates = []
    for pr in sorted(pulls, key=lambda item: int(item["number"])):
        if not trusted_candidate(pr):
            continue
        candidates.append(pr)
        if len(candidates) >= MAX_CANDIDATES:
            break

    for observed in candidates:
        pr = api(f"repos/{REPO}/pulls/{observed['number']}")
        sha = pr["head"]["sha"]
        if sha != observed["head"]["sha"] or not trusted_candidate(pr):
            continue

        issue_number = parse_issue_number(pr.get("body") or "")
        if not issue_number:
            stop_report(
                pr,
                None,
                "MISSING_TRUSTED_SOURCE_ISSUE",
                "PR body does not identify one trusted source Issue.",
            )
            continue

        issue = api(f"repos/{REPO}/issues/{issue_number}")
        files = api(f"repos/{REPO}/pulls/{pr['number']}/files?per_page=100")
        changed = [item["filename"] for item in files]
        protected = [path for path in changed if is_protected(path)]
        if protected and not protected_scope_is_authorized(
            changed, issue.get("body") or ""
        ):
            auto_close = any(
                label.get("name") == "e2e-auto-close"
                for label in pr.get("labels") or []
            )
            stop_report(
                pr,
                issue_number,
                "UNAUTHORIZED_PROTECTED_PATH",
                "Protected changed paths are not covered by Issue authorization.",
                close=auto_close,
            )
            continue

        states = trusted_checks_for(sha)
        required_contexts = set(REQUIRED_JOBS.values())
        if set(states) != required_contexts:
            continue
        if not all(states[context] == "success" for context in required_contexts):
            continue

        if not exact_codex_clean(pr["number"], sha):
            marker = f"<!-- foundation-codex-request:{sha} -->"
            comments = api(f"repos/{REPO}/issues/{pr['number']}/comments?per_page=100")
            if not any(marker in (item.get("body") or "") for item in comments):
                comment(
                    pr["number"],
                    f"{marker}\n@codex review\n\nReview exact head `{sha}`. "
                    "Report blocking security or correctness findings only.",
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


if __name__ == "__main__":
    main()

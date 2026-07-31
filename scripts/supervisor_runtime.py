#!/usr/bin/env python3
"""Trusted default-branch GitHub supervisor runtime.

The runtime inspects repository metadata only. It never checks out or executes
proposed-branch code. Merge authorization is bound to immutable exact-SHA
attestations produced by the fixed trusted reusable workflow.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any

from scripts.supervisor_policy import (
    is_protected,
    parse_issue_number,
    protected_scope_is_authorized,
)

REPO = os.environ["REPOSITORY"]
DEFAULT_BRANCH = os.environ["DEFAULT_BRANCH"]
AUTOMATION_OWNER = os.environ["AUTOMATION_OWNER"]
REQUIRED_CHECKS = {
    "CI / validate": "ci",
    "Unit Tests / test": "tests",
}
MAX_CANDIDATES = 10
MAX_CHANGED_FILES = 100
ALLOWED_PREFIXES = ("claude-issue-", "automation/", "fix/")
ALLOWED_AUTHORS = {AUTOMATION_OWNER, "github-actions[bot]"}
STOP_PREFIX = "<!-- foundation-stop:"
CODEX_LOGIN = "chatgpt-codex-connector[bot]"
RUN_URL_PATTERN = re.compile(
    rf"^https://github\.com/{re.escape(REPO)}/actions/runs/(\d+)$"
)


def gh(*args: str) -> str:
    return subprocess.check_output(["gh", *args], text=True)


def api(path: str) -> Any:
    return json.loads(
        gh("api", "-H", "Accept: application/vnd.github+json", path)
    )


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
        [
            "gh",
            "issue",
            "edit",
            str(number),
            "--repo",
            REPO,
            "--remove-label",
            label,
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def stop_report(pr: dict[str, Any], issue_number: int | None, reason: str, detail: str, close: bool = False) -> None:
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
            "- self_resolution_audit: metadata, changed-file completeness, scope, "
            "authorization, checks, review, provenance, idempotency, and available "
            "GitHub permissions were rechecked before stopping.\n"
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


def timestamp(item: dict[str, Any]) -> str:
    return str(
        item.get("created_at")
        or item.get("submitted_at")
        or item.get("updated_at")
        or ""
    )


def exact_codex_clean(pr_number: int, sha: str) -> bool:
    short = sha[:10]
    evidence: list[tuple[str, bool]] = []
    comments = api(f"repos/{REPO}/issues/{pr_number}/comments?per_page=100")
    reviews = api(f"repos/{REPO}/pulls/{pr_number}/reviews?per_page=100")
    for item in [*comments, *reviews]:
        login = (item.get("user") or {}).get("login") or ""
        if login != CODEX_LOGIN:
            continue
        body = item.get("body") or ""
        if short not in body and sha not in body:
            continue
        lower = body.lower()
        clean = "didn't find any major issues" in lower or "no major issues" in lower
        evidence.append((timestamp(item), clean))
    if not evidence:
        return False
    evidence.sort(key=lambda entry: entry[0])
    return evidence[-1][1]


def trusted_workflow_id() -> int:
    workflow = api(f"repos/{REPO}/actions/workflows/trusted-checks.yml")
    return int(workflow["id"])


def trusted_check_state(
    check: dict[str, Any], sha: str, kind: str, workflow_id: int
) -> str | None:
    if ((check.get("app") or {}).get("slug") != "github-actions"):
        return None
    if check.get("head_sha") != sha:
        return None
    match = RUN_URL_PATTERN.fullmatch(check.get("details_url") or "")
    if not match:
        return None
    run_id = int(match.group(1))
    if check.get("external_id") != (
        f"foundation:trusted-checks:{kind}:{sha}:{run_id}"
    ):
        return None
    try:
        run = api(f"repos/{REPO}/actions/runs/{run_id}")
    except subprocess.CalledProcessError:
        return None
    if int(run.get("workflow_id") or 0) != workflow_id:
        return None
    if run.get("event") != "workflow_call":
        return None
    if run.get("head_branch") != DEFAULT_BRANCH:
        return None
    if ((run.get("repository") or {}).get("full_name") != REPO):
        return None
    if run.get("display_title") != f"Trusted checks {sha}":
        return None
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        return None
    if check.get("status") != "completed":
        return None
    return str(check.get("conclusion") or "failure")


def trusted_checks_for(sha: str) -> dict[str, str]:
    workflow_id = trusted_workflow_id()
    check_runs = api(f"repos/{REPO}/commits/{sha}/check-runs?per_page=100").get(
        "check_runs", []
    )
    result: dict[str, str] = {}
    for name, kind in REQUIRED_CHECKS.items():
        for check in sorted(
            check_runs,
            key=lambda item: int(item.get("id") or 0),
            reverse=True,
        ):
            if check.get("name") != name:
                continue
            state = trusted_check_state(check, sha, kind, workflow_id)
            if state is not None:
                result[name] = state
                break
    return result


def trusted_candidate(pr: dict[str, Any]) -> bool:
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    author = (pr.get("user") or {}).get("login") or ""
    if ((head.get("repo") or {}).get("full_name") != REPO):
        return False
    if ((base.get("repo") or {}).get("full_name") != REPO):
        return False
    if base.get("ref") != DEFAULT_BRANCH:
        return False
    if author not in ALLOWED_AUTHORS:
        return False
    if not (head.get("ref") or "").startswith(ALLOWED_PREFIXES):
        return False
    if any(label.get("name") == "ai-no-merge" for label in pr.get("labels") or []):
        return False
    return True


def changed_paths(pr: dict[str, Any]) -> list[str] | None:
    expected = int(pr.get("changed_files") or 0)
    if expected > MAX_CHANGED_FILES:
        return None
    files = api(f"repos/{REPO}/pulls/{pr['number']}/files?per_page={MAX_CHANGED_FILES}")
    paths = [str(item["filename"]) for item in files]
    if len(paths) != expected:
        return None
    return paths


def request_codex(pr_number: int, sha: str) -> None:
    marker = f"<!-- foundation-codex-request:{sha} -->"
    comments = api(f"repos/{REPO}/issues/{pr_number}/comments?per_page=100")
    if any(marker in (item.get("body") or "") for item in comments):
        return
    comment(
        pr_number,
        f"{marker}\n@codex review\n\nReview exact head `{sha}`. "
        "Report blocking security or correctness findings only.",
    )


def main() -> None:
    pulls = api(f"repos/{REPO}/pulls?state=open&per_page=50")
    candidates: list[dict[str, Any]] = []
    for pr in sorted(pulls, key=lambda item: int(item["number"])):
        if not trusted_candidate(pr):
            continue
        candidates.append(pr)
        if len(candidates) >= MAX_CANDIDATES:
            break

    for observed in candidates:
        pr = api(f"repos/{REPO}/pulls/{observed['number']}")
        sha = (pr.get("head") or {}).get("sha") or ""
        if sha != (observed.get("head") or {}).get("sha"):
            continue
        if not trusted_candidate(pr):
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
        if issue.get("pull_request"):
            stop_report(
                pr,
                issue_number,
                "INVALID_TRUSTED_SOURCE_ISSUE",
                "The referenced source is a Pull Request rather than an Issue.",
            )
            continue

        paths = changed_paths(pr)
        if paths is None:
            stop_report(
                pr,
                issue_number,
                "INCOMPLETE_CHANGED_FILE_EVIDENCE",
                f"Changed-file evidence exceeded or did not match the bounded {MAX_CHANGED_FILES}-file snapshot.",
            )
            continue

        protected = [path for path in paths if is_protected(path)]
        if protected and not protected_scope_is_authorized(paths, issue.get("body") or ""):
            auto_close = any(
                label.get("name") == "e2e-auto-close"
                for label in pr.get("labels") or []
            )
            stop_report(
                pr,
                issue_number,
                "UNAUTHORIZED_PROTECTED_PATH",
                "Protected changed paths are not covered by exact Issue authorization.",
                close=auto_close,
            )
            continue

        states = trusted_checks_for(sha)
        if set(states) != set(REQUIRED_CHECKS):
            continue
        if not all(states[name] == "success" for name in REQUIRED_CHECKS):
            continue

        if not exact_codex_clean(pr["number"], sha):
            request_codex(pr["number"], sha)
            continue

        current = api(f"repos/{REPO}/pulls/{pr['number']}")
        if (current.get("head") or {}).get("sha") != sha:
            continue
        if not trusted_candidate(current):
            continue
        remove_label(pr["number"], "ai-blocked")
        if current.get("draft"):
            gh("pr", "ready", str(pr["number"]), "--repo", REPO)
            current = api(f"repos/{REPO}/pulls/{pr['number']}")
            if (current.get("head") or {}).get("sha") != sha:
                continue
            if not trusted_candidate(current):
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

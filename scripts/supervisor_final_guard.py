#!/usr/bin/env python3
"""Apply the minimum safety profile and bind the final expected-head merge."""
from __future__ import annotations

import re
from typing import Any

from scripts import supervisor_runtime as runtime
from scripts import supervisor_policy as policy

_native_workflow_evidence = runtime.native_workflow_evidence
_original_gh = runtime.gh
_original_current_default_sha = runtime.current_default_sha
_original_request_codex = runtime.request_codex
_original_exact_codex_evidence = runtime.exact_codex_evidence
_verified_gate: tuple[str, int, str, int] | None = None

COORDINATOR_REVIEW = re.compile(
    r"<!--\s*foundation-coordinator-review:([0-9a-f]{40}):clean\s*-->"
)
REVIEW_REQUIRED = "<!-- foundation-review-required:{sha}:{risk} -->"
PROVIDER_SETUP_PHRASES = (
    "create a codex account and connect to github",
    "create an environment for this repo",
    "connect to github",
)


def _exact_live_default_sha() -> str:
    sha = str(_original_current_default_sha())
    if not runtime.EXACT_SHA.fullmatch(sha):
        raise RuntimeError("Default branch did not resolve to one exact SHA")
    return sha


def _require_unchanged_default(expected_sha: str) -> str:
    live_sha = _exact_live_default_sha()
    if live_sha != expected_sha:
        raise RuntimeError("Default branch moved during final evidence validation")
    return expected_sha


def source_and_scope_minimum(
    pr: dict[str, Any],
) -> tuple[int | None, dict[str, Any] | None, list[str], str | None]:
    issue_number = policy.parse_issue_number(pr.get("body") or "")
    if not issue_number:
        return None, None, [], "MISSING_TRUSTED_SOURCE_ISSUE"
    issue = runtime.api(f"repos/{runtime.REPO}/issues/{issue_number}")
    if not runtime.trusted_source_issue(issue):
        return issue_number, issue, [], "UNTRUSTED_SOURCE_ISSUE"
    changed = runtime.changed_paths(pr)
    if changed is None:
        return issue_number, issue, [], "INCOMPLETE_CHANGED_FILE_EVIDENCE"
    issue_body = issue.get("body") or ""
    try:
        if not policy.scope_is_authorized(changed, issue_body):
            return issue_number, issue, changed, "UNAUTHORIZED_CHANGED_PATH"
        risk = policy.risk_for_changes(changed, issue_body)
        if risk == "protected" and not policy.protected_scope_is_authorized(
            changed, issue_body
        ):
            return issue_number, issue, changed, "UNAUTHORIZED_PROTECTED_PATH"
    except ValueError:
        return issue_number, issue, changed, "UNAUTHORIZED_PROTECTED_PATH"
    return issue_number, issue, changed, None


# Compatibility alias during the validator migration. The active implementation
# is the unified minimum-safety contract: source_and_scope(live_pr).
source_and_scope = source_and_scope_minimum


def _authorized_source_snapshot(
    live_pr: dict[str, Any], candidate_sha: str
) -> tuple[int, dict[str, Any]]:
    live_head = str((live_pr.get("head") or {}).get("sha") or "")
    if not isinstance(live_pr.get("labels"), list):
        raise RuntimeError("Live Pull Request omitted explicit label evidence")
    if live_head != candidate_sha or not runtime.trusted_candidate(live_pr):
        raise RuntimeError("Live Pull Request no longer matches the trusted candidate")
    issue_number, issue, _, scope_error = source_and_scope(live_pr)
    if (
        scope_error
        or not isinstance(issue_number, int)
        or issue_number <= 0
        or not isinstance(issue, dict)
    ):
        raise RuntimeError("Live source and scope authorization no longer passes")
    return issue_number, issue


def _authorized_source_issue(live_pr: dict, candidate_sha: str) -> int:
    return _authorized_source_snapshot(live_pr, candidate_sha)[0]


def _successful_exact_head_check_names(
    sha: str, native_evidence: list[dict[str, Any]]
) -> set[str]:
    if not runtime.EXACT_SHA.fullmatch(sha):
        raise ValueError("Required checks need one exact candidate SHA")
    names: set[str] = set()
    for item in native_evidence:
        for key in ("workflow", "display_name", "name"):
            value = str(item.get(key) or "").strip()
            if value:
                names.add(value)
    check_runs = runtime.api_key_pages(
        f"repos/{runtime.REPO}/commits/{sha}/check-runs?per_page=100",
        "check_runs",
    )
    for item in check_runs:
        if item.get("status") != "completed" or item.get("conclusion") != "success":
            continue
        name = str(item.get("name") or "").strip()
        if name:
            names.add(name)
    return names


def _missing_required_task_checks(
    issue_body: str, sha: str, native_evidence: list[dict[str, Any]]
) -> list[str]:
    scope = policy.parse_task_scope(issue_body)
    if scope is None:
        return []
    successful = _successful_exact_head_check_names(sha, native_evidence)
    return [check for check in scope.checks if check not in successful]


def _risk_for_pr(pr_number: int, sha: str) -> tuple[str, int]:
    live_pr = runtime._live_pr(pr_number, sha)
    issue_number, issue, changed, scope_error = source_and_scope_minimum(live_pr)
    if scope_error or issue is None or not isinstance(issue_number, int):
        raise RuntimeError("Review tier requires a current trusted task scope")
    risk = policy.risk_for_changes(changed, issue.get("body") or "")
    return risk, issue_number


def _provider_route_unavailable(pr_number: int) -> bool:
    for item in reversed(runtime._codex_items(pr_number)):
        if (item.get("user") or {}).get("login") != runtime.CODEX_LOGIN:
            continue
        body = str(item.get("body") or "").lower()
        if any(phrase in body for phrase in PROVIDER_SETUP_PHRASES):
            return True
    return False


def _coordinator_review(pr_number: int, sha: str) -> dict[str, str | None] | None:
    trusted_authors = set(runtime.TRUSTED_ISSUE_AUTHORS)
    for item in reversed(runtime.api_list(f"repos/{runtime.REPO}/issues/{pr_number}/comments?per_page=100")):
        login = (item.get("user") or {}).get("login") or ""
        if login not in trusted_authors:
            continue
        if item.get("created_at") != item.get("updated_at"):
            continue
        body = str(item.get("body") or "")
        match = COORDINATOR_REVIEW.search(body)
        if not match or match.group(1) != sha:
            continue
        summary = body[match.end() :].strip()
        if not summary:
            continue
        return {
            "state": "clean",
            "timestamp": item.get("created_at"),
            "request_timestamp": item.get("created_at"),
            "review_source": "coordinator",
        }
    return None


def review_evidence(pr_number: int, sha: str) -> dict[str, str | None]:
    if not isinstance(pr_number, int) or pr_number <= 0:
        raise ValueError("Pull Request number must be a positive integer")
    if not runtime.EXACT_SHA.fullmatch(sha):
        raise ValueError("Review evidence requires one exact candidate SHA")
    risk, _ = _risk_for_pr(pr_number, sha)
    unresolved = runtime.unresolved_review_threads(pr_number)
    codex = dict(_original_exact_codex_evidence(pr_number, sha))
    codex["review_source"] = "codex"
    codex["risk"] = risk
    if unresolved:
        codex["state"] = "blocking"
        return codex
    if risk == "low":
        return {
            "state": "clean",
            "timestamp": None,
            "request_timestamp": None,
            "review_source": "low-risk-checks",
            "risk": risk,
        }
    if codex.get("state") == "clean":
        return codex
    if risk == "standard":
        coordinator = _coordinator_review(pr_number, sha)
        if coordinator is not None:
            coordinator["risk"] = risk
            return coordinator
    if codex.get("state") == "pending" and _provider_route_unavailable(pr_number):
        codex["review_route"] = "unavailable"
    return codex


def record_review_required(pr_number: int, sha: str) -> None:
    risk, _ = _risk_for_pr(pr_number, sha)
    if risk == "low":
        return
    marker = REVIEW_REQUIRED.format(sha=sha, risk=risk)
    comments = runtime.api_list(
        f"repos/{runtime.REPO}/issues/{pr_number}/comments?per_page=100"
    )
    if any(
        (item.get("user") or {}).get("login") == runtime.ACTIONS_LOGIN
        and item.get("created_at") == item.get("updated_at")
        and marker in str(item.get("body") or "")
        for item in comments
    ):
        return
    route = "Codex or trusted coordinator" if risk == "standard" else "owner/connector Codex"
    runtime.comment(
        pr_number,
        f"{marker}\nREVIEW_REQUIRED for exact head `{sha}`. "
        f"Risk tier: `{risk}`. Active route: {route}. This neutral marker does not invoke a provider.",
    )


def request_codex_exact_head(pr_number: int, sha: str) -> None:
    """Legacy callable retained for compatibility; the active runtime uses a neutral marker."""
    if not isinstance(pr_number, int) or pr_number <= 0:
        raise ValueError("Pull Request number must be a positive integer")
    if not runtime.EXACT_SHA.fullmatch(sha):
        raise ValueError("Codex review request requires one exact candidate SHA")
    _original_request_codex(pr_number, sha)


def guarded_native_workflow_evidence(sha: str, pr_number: int):
    """Evaluate stable checks and task authorization before the final live recheck."""
    global _verified_gate
    _verified_gate = None
    default_sha = _exact_live_default_sha()
    previous_current_default_sha = runtime.current_default_sha
    runtime.current_default_sha = lambda: _require_unchanged_default(default_sha)
    try:
        attempts = runtime.attestation_attempts(sha)
        if not any(item["success"] for item in attempts):
            return False, []
        clean, evidence = _native_workflow_evidence(sha, pr_number)
        if not clean:
            return False, evidence
        live_pr = runtime.api(f"repos/{runtime.REPO}/pulls/{pr_number}")
        issue_number, issue = _authorized_source_snapshot(live_pr, sha)
        missing_checks = _missing_required_task_checks(
            issue.get("body") or "", sha, evidence
        )
        if missing_checks:
            return False, [
                *evidence,
                {"missing_task_checks": missing_checks, "exact_head_sha": sha},
            ]
        _require_unchanged_default(default_sha)
        _verified_gate = (sha, pr_number, default_sha, issue_number)
        return True, evidence
    except (RuntimeError, ValueError):
        _verified_gate = None
        return False, []
    finally:
        runtime.current_default_sha = previous_current_default_sha


def _merge_identity(args: tuple[str, ...]) -> tuple[str, int] | None:
    if not args or args[0] != "api":
        return None
    method = None
    path = None
    candidate_sha = None
    for index, value in enumerate(args):
        if value == "--method" and index + 1 < len(args):
            method = args[index + 1]
        if value.startswith(f"repos/{runtime.REPO}/pulls/") and value.endswith("/merge"):
            path = value
        if value == "-f" and index + 1 < len(args):
            field = args[index + 1]
            if field.startswith("sha="):
                candidate_sha = field.removeprefix("sha=")
    if method != "PUT" or path is None:
        return None
    if candidate_sha is None or not runtime.EXACT_SHA.fullmatch(candidate_sha):
        raise RuntimeError("Merge call omitted its exact candidate SHA")
    prefix = f"repos/{runtime.REPO}/pulls/"
    number_text = path.removeprefix(prefix).removesuffix("/merge")
    if not number_text.isdigit() or int(number_text) <= 0:
        raise RuntimeError("Merge call omitted its fixed Pull Request number")
    return candidate_sha, int(number_text)


def guarded_gh(*args: str) -> str:
    """Perform the final live recheck and clear eligibility only after merge succeeds."""
    global _verified_gate
    identity = _merge_identity(args)
    if identity is None:
        return _original_gh(*args)
    if _verified_gate is None:
        raise RuntimeError("Merge call has no verified final evidence gate")
    gate = _verified_gate
    candidate_sha, pr_number = identity
    verified_sha, verified_pr, default_sha, verified_issue = gate
    if (candidate_sha, pr_number) != (verified_sha, verified_pr):
        raise RuntimeError("Merge call does not match the verified candidate gate")
    live_pr = runtime.api(f"repos/{runtime.REPO}/pulls/{pr_number}")
    if (
        live_pr.get("state") != "open"
        or live_pr.get("draft") is not False
        or live_pr.get("mergeable") is not True
    ):
        raise RuntimeError("Live Pull Request no longer matches the trusted merge gate")
    live_issue, issue = _authorized_source_snapshot(live_pr, candidate_sha)
    if live_issue != verified_issue:
        raise RuntimeError("Live trusted source Issue no longer matches the verified gate")

    native_clean, native_evidence = _native_workflow_evidence(
        candidate_sha, pr_number
    )
    if not native_clean:
        raise RuntimeError("Required exact-head native checks no longer pass")
    missing_checks = _missing_required_task_checks(
        issue.get("body") or "", candidate_sha, native_evidence
    )
    if missing_checks:
        raise RuntimeError(
            f"Live Issue requires missing exact-head checks: {missing_checks}"
        )
    attempts = runtime.attestation_attempts(candidate_sha)
    if not any(item.get("success") is True for item in attempts):
        raise RuntimeError("Required exact-head attestation no longer passes")
    if runtime.unresolved_review_threads(pr_number):
        raise RuntimeError("Live Pull Request has unresolved review threads")
    live_review = review_evidence(pr_number, candidate_sha)
    if live_review.get("state") != "clean":
        raise RuntimeError("Live risk-tier review evidence no longer passes")

    _require_unchanged_default(default_sha)
    result = _original_gh(*args)
    _verified_gate = None
    return result


def main() -> int:
    runtime.source_and_scope = source_and_scope_minimum
    runtime.exact_codex_evidence = review_evidence
    runtime.exact_codex_state = lambda pr_number, sha: str(
        review_evidence(pr_number, sha)["state"]
    )
    runtime.exact_codex_clean = lambda pr_number, sha: (
        review_evidence(pr_number, sha)["state"] == "clean"
    )
    runtime.request_codex = record_review_required
    runtime.native_workflow_evidence = guarded_native_workflow_evidence
    runtime.gh = guarded_gh
    return runtime.main()


if __name__ == "__main__":
    raise SystemExit(main())

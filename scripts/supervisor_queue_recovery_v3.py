#!/usr/bin/env python3
"""Bind Queue recovery to exact request/default identity and complete scope audit."""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
from typing import Any, Callable

from scripts import supervisor_policy as policy
from scripts import supervisor_queue_recovery as recovery
from scripts import supervisor_queue_recovery_v2 as hardened
from scripts import supervisor_runtime as runtime
from scripts.queue_failure_classifier import (
    FailureClass,
    build_failure_status,
    classify_conclusion,
    should_auto_retry,
)

_original_connected_exhaustion_snapshot = hardened._connected_exhaustion_snapshot
_original_intent_identity = hardened._intent_identity
_original_dispatch_fixed_retry = hardened._dispatch_fixed_retry


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _exact_default_sha() -> str:
    sha = str(runtime.current_default_sha())
    if not runtime.EXACT_SHA.fullmatch(sha):
        raise RuntimeError("Queue request default branch did not resolve exactly")
    return sha


def content_bound_request_fingerprint(issue_number: int, timestamp: str) -> str:
    issue = runtime.api(f"repos/{runtime.REPO}/issues/{issue_number}")
    if not recovery._trusted_issue(issue):
        raise RuntimeError("Queue request is no longer a trusted open Issue")
    body = str(issue.get("body") or "")
    trigger: dict[str, Any]
    if recovery._first_effective_line(body) == recovery.QUEUE_TRIGGER:
        created_at = str(issue.get("created_at") or "")
        if not created_at or timestamp != created_at:
            raise RuntimeError("Queue body trigger timestamp no longer matches")
        trigger = {
            "author": (issue.get("user") or {}).get("login") or "",
            "created_at": created_at,
            "kind": "issue-body",
            "text": recovery.QUEUE_TRIGGER,
        }
    else:
        comments = runtime.api_list(
            f"repos/{runtime.REPO}/issues/{issue_number}/comments?per_page=100"
        )
        matches = [
            comment
            for comment in comments
            if (comment.get("user") or {}).get("login")
            in runtime.TRUSTED_ISSUE_AUTHORS
            and str(comment.get("body") or "").strip() == recovery.QUEUE_TRIGGER
            and str(comment.get("created_at") or "") == timestamp
        ]
        if len(matches) != 1:
            raise RuntimeError("Queue comment trigger identity is missing or ambiguous")
        comment = matches[0]
        trigger = {
            "author": (comment.get("user") or {}).get("login") or "",
            "comment_id": int(comment.get("id") or 0),
            "created_at": timestamp,
            "kind": "issue-comment",
            "text": str(comment.get("body") or "").strip(),
            "updated_at": str(comment.get("updated_at") or ""),
        }
        if trigger["comment_id"] <= 0:
            raise RuntimeError("Queue comment trigger omitted its immutable ID")
    labels = sorted(
        str(label.get("name") or "")
        for label in issue.get("labels") or []
        if isinstance(label, dict) and label.get("name")
    )
    payload = {
        "default_sha": _exact_default_sha(),
        "issue_author": (issue.get("user") or {}).get("login") or "",
        "issue_body": body,
        "issue_number": issue_number,
        "issue_title": str(issue.get("title") or ""),
        "labels": labels,
        "request_timestamp": timestamp,
        "trigger": trigger,
    }
    return hashlib.sha256(_canonical(payload)).hexdigest()[:20]


def _validated_issue_scope(issue_number: int) -> dict[str, Any]:
    issue = runtime.api(f"repos/{runtime.REPO}/issues/{issue_number}")
    if not recovery._trusted_issue(issue):
        raise RuntimeError("Queue source Issue is no longer trusted")
    body = str(issue.get("body") or "")
    declared = sorted(policy.declared_paths(body))
    protected = sorted(policy.protected_authorized_paths(body))
    if not declared or not policy.scope_is_authorized(declared, body):
        raise RuntimeError("Queue source Issue ordinary scope is missing or invalid")
    if any(policy.is_protected(path) for path in declared) and not policy.protected_scope_is_authorized(
        declared, body
    ):
        raise RuntimeError("Queue source Issue protected scope is missing or invalid")
    return {
        "declared_paths": declared,
        "protected_authorized_paths": protected,
    }


def _all_trusted_open_candidates() -> list[dict[str, Any]]:
    """Return complete current Pull Request records without supervision caps."""
    summaries = runtime.api_list(
        f"repos/{runtime.REPO}/pulls?state=open&per_page=100"
    )
    candidates: list[dict[str, Any]] = []
    for summary in sorted(
        summaries, key=lambda item: int(item.get("number") or 0)
    ):
        if not runtime.trusted_candidate(summary):
            continue
        number = int(summary.get("number") or 0)
        if number <= 0:
            raise RuntimeError("Trusted candidate summary omitted its Pull Request number")
        live = runtime.api(f"repos/{runtime.REPO}/pulls/{number}")
        if int(live.get("number") or 0) != number:
            raise RuntimeError("Live Pull Request record does not match its summary")
        changed_files = live.get("changed_files")
        if (
            live.get("state") != "open"
            or not isinstance(live.get("labels"), list)
            or isinstance(changed_files, bool)
            or not isinstance(changed_files, int)
            or changed_files < 0
        ):
            raise RuntimeError("Live Pull Request record is incomplete or no longer open")
        if runtime.trusted_candidate(live):
            candidates.append(live)
    return candidates


def _trusted_alternative_candidates(issue_number: int) -> list[int]:
    candidates: list[int] = []
    for pull in _all_trusted_open_candidates():
        source_issue, _, _, error = runtime.source_and_scope(pull)
        if source_issue == issue_number and error is None:
            candidates.append(int(pull.get("number") or 0))
    return sorted(number for number in candidates if number > 0)


def require_no_trusted_alternative(issue_number: int) -> None:
    alternatives = _trusted_alternative_candidates(issue_number)
    if alternatives:
        raise RuntimeError(
            "Trusted alternative candidate already exists: "
            + ",".join(str(number) for number in alternatives)
        )


def intent_identity_without_alternative(
    issue_number: int, fingerprint: str, attempt: int
):
    """Validate current scope before retry persistence and suppress alternatives."""
    _validated_issue_scope(issue_number)
    require_no_trusted_alternative(issue_number)
    return _original_intent_identity(issue_number, fingerprint, attempt)


def dispatch_without_alternative(
    issue_number: int,
    fingerprint: str,
    attempt: int,
    expected_default_sha: str,
) -> None:
    """Revalidate current scope and alternative work immediately before dispatch."""
    _validated_issue_scope(issue_number)
    require_no_trusted_alternative(issue_number)
    _original_dispatch_fixed_retry(
        issue_number, fingerprint, attempt, expected_default_sha
    )


def wait_for_admitted_implementation(
    issue_number: int,
    fingerprint: str,
    attempt: int,
    expected_default_sha: str,
) -> int:
    """Return after implementation starts, including a fast terminal failure."""
    deadline = time.monotonic() + hardened.QUEUE_START_TIMEOUT_SECONDS
    selected_run_id: int | None = None
    while time.monotonic() < deadline:
        matches = hardened._matching_dispatch_runs(
            issue_number, fingerprint, attempt, expected_default_sha
        )
        if len(matches) > 1:
            raise RuntimeError("Queue retry dispatch produced ambiguous workflow runs")
        if matches:
            selected_run_id = int(matches[0]["id"])
            jobs = runtime.api_key_pages(
                f"repos/{runtime.REPO}/actions/runs/{selected_run_id}/jobs?filter=all&per_page=100",
                "jobs",
            )
            prepare = [job for job in jobs if job.get("name") == "prepare"]
            implement = [job for job in jobs if job.get("name") == "implement"]
            if len(prepare) > 1 or len(implement) > 1:
                raise RuntimeError("Queue retry jobs are ambiguous")
            if prepare and prepare[0].get("status") == "completed":
                if prepare[0].get("conclusion") != "success":
                    raise RuntimeError("Queue retry prepare admission did not succeed")
                if implement:
                    status = str(implement[0].get("status") or "")
                    conclusion = str(implement[0].get("conclusion") or "")
                    started_at = str(implement[0].get("started_at") or "")
                    if status == "in_progress":
                        return selected_run_id
                    if status == "completed":
                        if conclusion == "success":
                            return selected_run_id
                        if conclusion == "failure" and started_at:
                            return selected_run_id
                        if conclusion == "failure":
                            raise RuntimeError(
                                "Queue retry failure omitted implementation start evidence"
                            )
                        if conclusion in {"cancelled", "skipped"}:
                            raise RuntimeError(
                                "Queue retry implementation was not admitted"
                            )
            if matches[0].get("status") == "completed":
                raise RuntimeError(
                    "Queue retry completed without an admitted implementation job"
                )
        time.sleep(hardened.QUEUE_START_POLL_SECONDS)
    raise RuntimeError(
        "Queue retry did not start while supervisor remained active: "
        f"{selected_run_id or 'unresolved'}"
    )


def complete_connected_exhaustion_snapshot(
    issue_number: int,
    fingerprint: str,
    expected_default_sha: str,
    expected_retry_records: list[str],
) -> dict[str, Any]:
    if _exact_default_sha() != expected_default_sha:
        raise RuntimeError("Default branch moved before complete Queue audit")
    scope = _validated_issue_scope(issue_number)
    alternatives = _trusted_alternative_candidates(issue_number)
    if alternatives:
        raise RuntimeError("Trusted alternative candidate path remains available")
    snapshot = _original_connected_exhaustion_snapshot(
        issue_number,
        fingerprint,
        expected_default_sha,
        expected_retry_records,
    )
    if _exact_default_sha() != expected_default_sha:
        raise RuntimeError("Default branch moved during complete Queue audit")
    final_alternatives = _trusted_alternative_candidates(issue_number)
    if final_alternatives:
        raise RuntimeError(
            "Trusted alternative candidate appeared during complete Queue audit"
        )
    snapshot.update(
        {
            "alternative_candidate_prs": final_alternatives,
            "alternative_paths_exhausted": not final_alternatives,
            "source_declared_paths": scope["declared_paths"],
            "source_protected_authorized_paths": scope[
                "protected_authorized_paths"
            ],
            "source_issue_authorization_verified": True,
        }
    )

    # Failure evidence and checkpoint discovery are optional enrichment for the
    # sanitized stop snapshot. If local GitHub CLI access is unavailable, retain
    # every validated exhaustion gate and report a deterministic unknown/no-checkpoint
    # state instead of aborting snapshot generation.
    attempt_count = len(expected_retry_records)
    try:
        evidence = _latest_run_failure_evidence(issue_number)
    except (OSError, subprocess.CalledProcessError):
        evidence = None
    if evidence is not None:
        conclusion, perm_denials, error_detail = evidence
        failure_class = classify_conclusion(conclusion, perm_denials, error_detail)
    else:
        failure_class = FailureClass.UNKNOWN
    try:
        checkpoint = _wip_branch_info(issue_number)
    except (OSError, subprocess.CalledProcessError):
        checkpoint = None
    checkpoint_branch = checkpoint[0] if checkpoint is not None else None
    checkpoint_sha = checkpoint[1] if checkpoint is not None else None
    status = build_failure_status(
        failure_class=failure_class,
        retry_attempt=attempt_count,
        max_retries=recovery.MAX_QUEUE_RECOVERY_ATTEMPTS,
        checkpoint_sha=checkpoint_sha,
    )
    snapshot.update(
        {
            "checkpoint_branch": checkpoint_branch,
            "checkpoint_sha": checkpoint_sha,
            "failure_class": status.failure_class.value,
            "human_action_required": status.human_action_required,
            "next_automatic_action": status.next_automatic_action,
            "retry_attempt": status.retry_attempt,
        }
    )
    return snapshot


def _wip_branch_info(issue_number: int) -> tuple[str, str] | None:
    """Return (branch_name, sha) for an existing same-issue WIP/checkpoint branch, or None.

    Checks GitHub branches for names starting with 'claude-issue-{N}-', which indicates
    an in-progress or checkpointed implementation for this issue exists without a PR.
    """
    prefix = f"claude-issue-{issue_number}-"
    try:
        result = runtime.gh_result(
            "api",
            "-H",
            "Accept: application/vnd.github+json",
            f"repos/{runtime.REPO}/branches?per_page=100",
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    branches = json.loads(result.stdout)
    if not isinstance(branches, list):
        return None
    for branch in branches:
        name = str(branch.get("name") or "")
        if name.startswith(prefix):
            sha = str((branch.get("commit") or {}).get("sha") or "")
            if runtime.EXACT_SHA.fullmatch(sha):
                return name, sha
    return None


def _latest_run_failure_evidence(
    issue_number: int,
) -> tuple[str, int, str] | None:
    """Return (conclusion, permission_denials_count, error_detail) from the latest Queue run.

    Scans completed failed Queue workflow runs on the default branch and returns
    implement-job evidence for the most recent run relevant to this issue.
    Returns None when no applicable run is found.
    """
    latest_run_id: int | None = None
    latest_run_number: int = 0

    for run in recovery._queue_runs():
        run_repo = (run.get("repository") or {}).get("full_name")
        run_path = str(run.get("path") or "").split("@", 1)[0]
        run_number = int(run.get("run_number") or 0)
        title = str(run.get("display_title") or "")
        if (
            run_repo != runtime.REPO
            or run_path != recovery.QUEUE_WORKFLOW_PATH
            or run.get("head_branch") != runtime.DEFAULT_BRANCH
            or run.get("status") != "completed"
            or run.get("conclusion") in {"success", "skipped", "neutral"}
        ):
            continue
        # Recovery runs embed the issue number in their display title.
        # Original runs triggered by issue events are also relevant.
        is_for_issue = (
            f"issue-{issue_number} " in title
            or run.get("event") in {"issues", "issue_comment"}
        )
        if not is_for_issue:
            continue
        run_id = int(run.get("id") or 0)
        if run_id > 0 and run_number > latest_run_number:
            latest_run_number = run_number
            latest_run_id = run_id

    if latest_run_id is None:
        return None

    jobs = runtime.api_key_pages(
        f"repos/{runtime.REPO}/actions/runs/{latest_run_id}/jobs?filter=all&per_page=100",
        "jobs",
    )
    implement = [job for job in jobs if job.get("name") == "implement"]
    if len(implement) != 1 or implement[0].get("status") != "completed":
        return None

    impl_job = implement[0]
    conclusion = str(impl_job.get("conclusion") or "")
    perm_denials = 0
    error_detail_parts: list[str] = []

    for step in impl_job.get("steps") or []:
        step_name = str(step.get("name") or "").lower()
        step_conclusion = str(step.get("conclusion") or "")
        if step_conclusion == "failure":
            error_detail_parts.append(step_name)
            if any(
                sig in step_name
                for sig in (
                    "tool policy",
                    "tool permission",
                    "not allowed",
                    "permission denied",
                    "allowedtools",
                    "allowed tools",
                )
            ):
                perm_denials += 1

    return conclusion, perm_denials, "; ".join(error_detail_parts)


def _classified_dispatch_retry(
    issue_number: int,
    fingerprint: str,
    attempt: int,
    v2_dispatch: Callable[..., bool],
) -> bool:
    """Pre-check failure class and WIP branch before allowing dispatch.

    Returns False without dispatching when:
    - an existing same-issue WIP/checkpoint branch is detected (requirement 7), or
    - the latest run's failure class is non-retryable (requirements 4-6).
    Otherwise delegates to the v2 guarded dispatch.
    """
    # Requirement 7: detect existing WIP/checkpoint branch even without a PR.
    checkpoint = _wip_branch_info(issue_number)
    if checkpoint is not None:
        return False

    # Requirements 1-3: inspect the latest Queue run and classify its failure.
    evidence = _latest_run_failure_evidence(issue_number)
    if evidence is not None:
        conclusion, perm_denials, error_detail = evidence
        failure_class = classify_conclusion(conclusion, perm_denials, error_detail)
    else:
        failure_class = FailureClass.UNKNOWN

    # Requirements 4-6: only dispatch for retryable automation-owned classes.
    if not should_auto_retry(failure_class, attempt - 1, recovery.MAX_QUEUE_RECOVERY_ATTEMPTS):
        return False

    return v2_dispatch(issue_number, fingerprint, attempt)


def main() -> int:
    recovery._request_fingerprint = content_bound_request_fingerprint
    hardened._intent_identity = intent_identity_without_alternative
    hardened._dispatch_fixed_retry = dispatch_without_alternative
    hardened._wait_for_queue_implementation_start = wait_for_admitted_implementation
    hardened._connected_exhaustion_snapshot = complete_connected_exhaustion_snapshot

    # Install the classified dispatch wrapper. v2's main() sets recovery._dispatch_retry
    # to guarded_dispatch_retry before calling recovery.main(). We intercept recovery.main
    # so we can wrap that assignment with our classification pre-check.
    _original_recovery_main = recovery.main

    def _recovery_main_with_classification() -> int:
        _v2_dispatch = recovery._dispatch_retry

        def _classified(iss: int, fp: str, att: int) -> bool:
            return _classified_dispatch_retry(iss, fp, att, _v2_dispatch)

        recovery._dispatch_retry = _classified
        return _original_recovery_main()

    recovery.main = _recovery_main_with_classification
    try:
        return hardened.main()
    finally:
        recovery.main = _original_recovery_main


if __name__ == "__main__":
    raise SystemExit(main())

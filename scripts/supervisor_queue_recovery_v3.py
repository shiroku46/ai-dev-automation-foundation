#!/usr/bin/env python3
"""Bind Queue recovery to exact request/default identity and complete scope audit."""
from __future__ import annotations

import hashlib
import json
import re
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


def _parse_failure_evidence(
    evidence: tuple[str, int, str] | tuple[str, int, str, int] | None,
) -> tuple[FailureClass, int | None]:
    if evidence is None:
        return FailureClass.UNKNOWN, None
    conclusion, permission_denials, error_detail = evidence[:3]
    run_id = int(evidence[3]) if len(evidence) > 3 else None
    return classify_conclusion(conclusion, permission_denials, error_detail), run_id


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
    snapshot.update(
        {
            "alternative_candidate_prs": alternatives,
            "alternative_paths_exhausted": not alternatives,
            "source_declared_paths": scope["declared_paths"],
            "source_protected_authorized_paths": scope[
                "protected_authorized_paths"
            ],
            "source_issue_authorization_verified": True,
        }
    )

    attempt_count = len(expected_retry_records)
    try:
        evidence = _latest_run_failure_evidence(
            issue_number, fingerprint, expected_default_sha
        )
    except (OSError, RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError):
        evidence = None
    failure_class, _ = _parse_failure_evidence(evidence)
    try:
        checkpoint = _wip_branch_info(issue_number, strict=False)
    except (OSError, RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError):
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

    if _exact_default_sha() != expected_default_sha:
        raise RuntimeError("Default branch moved after Queue exhaustion enrichment")
    post_enrichment_alternatives = _trusted_alternative_candidates(issue_number)
    if post_enrichment_alternatives:
        raise RuntimeError(
            "Trusted alternative candidate appeared during complete Queue audit"
        )
    if _exact_default_sha() != expected_default_sha:
        raise RuntimeError("Default branch moved after final alternative audit")
    snapshot["alternative_candidate_prs"] = post_enrichment_alternatives
    snapshot["alternative_paths_exhausted"] = True
    return snapshot


def _wip_branch_info(
    issue_number: int, *, strict: bool = False
) -> tuple[str, str] | None:
    """Return one deterministic same-Issue checkpoint branch, if present.

    Dispatch admission uses strict mode: pagination/API failures stop recovery rather
    than allowing a duplicate default-branch retry. Exhaustion enrichment may use
    best-effort mode because it is status-only and is followed by exact-head audits.
    """
    prefix = f"claude-issue-{issue_number}-"
    try:
        branches = runtime.api_list(
            f"repos/{runtime.REPO}/branches?per_page=100"
        )
    except (OSError, RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        if strict:
            raise RuntimeError("Checkpoint branch enumeration failed closed") from exc
        return None
    if not isinstance(branches, list):
        if strict:
            raise RuntimeError("Checkpoint branch enumeration was incomplete")
        return None

    matches: list[tuple[str, str]] = []
    for branch in branches:
        name = str(branch.get("name") or "")
        if not name.startswith(prefix):
            continue
        sha = str((branch.get("commit") or {}).get("sha") or "")
        if not runtime.EXACT_SHA.fullmatch(sha):
            if strict:
                raise RuntimeError("Checkpoint branch omitted an exact SHA")
            continue
        matches.append((name, sha))
    return sorted(matches)[0] if matches else None


def _title_identifies_issue(title: str, issue_number: int) -> bool:
    return bool(
        re.search(
            rf"(?<![0-9])issue-{issue_number}(?![0-9])",
            title,
            flags=re.IGNORECASE,
        )
    )


def _job_stage(name: str) -> str | None:
    normalized = name.strip().lower()
    for stage in ("prepare", "resolve", "implement", "verify", "publish", "finalize"):
        if normalized == stage or normalized.startswith(stage + " ") or normalized.startswith(stage + " /"):
            return stage
    return None


def _validated_request_timestamp(issue_number: int, fingerprint: str) -> str:
    issue = recovery._revalidate_request(issue_number, fingerprint)
    timestamp = recovery._request_timestamp(issue)
    if not timestamp:
        raise RuntimeError("Queue request timestamp is unavailable")
    return timestamp


def _run_matches_request(
    run: dict[str, Any],
    issue_number: int,
    fingerprint: str,
    expected_default_sha: str,
    request_timestamp: str,
) -> bool:
    run_repo = (run.get("repository") or {}).get("full_name")
    run_path = str(run.get("path") or "").split("@", 1)[0]
    title = str(run.get("display_title") or "")
    event = str(run.get("event") or "")
    actor = (run.get("actor") or {}).get("login") or ""
    created_at = str(run.get("created_at") or "")
    if (
        run_repo != runtime.REPO
        or run_path != recovery.QUEUE_WORKFLOW_PATH
        or run.get("head_branch") != runtime.DEFAULT_BRANCH
        or run.get("head_sha") != expected_default_sha
        or run.get("status") != "completed"
        or run.get("conclusion") in {"success", "skipped", "neutral", ""}
        or actor not in runtime.ALLOWED_AUTHORS
        or not _title_identifies_issue(title, issue_number)
        or not created_at
        or created_at < request_timestamp
    ):
        return False
    if event == "workflow_dispatch":
        return f"request-{fingerprint}" in title
    return event in {"issues", "issue_comment"}


def _job_log_failure_markers(job_id: int) -> list[str]:
    """Extract only known classification markers from a failed job log."""
    if job_id <= 0:
        return []
    result = runtime.gh_result(
        "api",
        "-H",
        "Accept: application/vnd.github+json",
        f"repos/{runtime.REPO}/actions/jobs/{job_id}/logs",
    )
    if result.returncode != 0:
        return []
    low = f"{result.stdout}\n{result.stderr}".lower()
    marker_signals: tuple[tuple[str, tuple[str, ...]], ...] = (
        (
            "error_max_turns",
            (
                "error_max_turns",
                "max turns",
                "maximum number of turns",
                "turn limit",
            ),
        ),
        (
            "tool policy",
            (
                "tool policy",
                "tool permission",
                "allowedtools",
                "allowed tools",
                "command is not allowed",
                "not permitted by tool",
            ),
        ),
        (
            "authentication failed",
            (
                "token expired",
                "expired token",
                "authentication failed",
                "missing secret",
                "secret not found",
                "unauthorized",
                "session limit",
                "credential expired",
            ),
        ),
        (
            "connect tunnel",
            (
                "connect tunnel",
                "could not resolve host",
                "connection reset",
                "connection refused",
                "network is unreachable",
                "git transport",
            ),
        ),
        (
            "test failure",
            (
                "failed (failures=",
                "failed (errors=",
                "assertionerror",
                "test failure",
                "tests failed",
            ),
        ),
        (
            "service unavailable",
            (
                "service unavailable",
                "runner lost communication",
                "503 service",
                "timed out",
            ),
        ),
    )
    return [
        marker
        for marker, signals in marker_signals
        if any(signal in low for signal in signals)
    ]


def _bound_failure_evidence(
    issue_number: int,
    fingerprint: str,
    expected_default_sha: str,
) -> tuple[str, int, str, int] | None:
    request_timestamp = _validated_request_timestamp(issue_number, fingerprint)
    if _exact_default_sha() != expected_default_sha:
        raise RuntimeError("Default branch moved before Queue failure classification")
    latest_run: dict[str, Any] | None = None
    latest_key = (-1, -1)
    for run in recovery._queue_runs():
        if not _run_matches_request(
            run,
            issue_number,
            fingerprint,
            expected_default_sha,
            request_timestamp,
        ):
            continue
        run_id = int(run.get("id") or 0)
        key = (int(run.get("run_number") or 0), run_id)
        if run_id > 0 and key > latest_key:
            latest_key = key
            latest_run = run

    if latest_run is None:
        return None
    latest_run_id = int(latest_run["id"])
    jobs = runtime.api_key_pages(
        f"repos/{runtime.REPO}/actions/runs/{latest_run_id}/jobs?filter=all&per_page=100",
        "jobs",
    )
    stage_order = {
        "prepare": 0,
        "resolve": 1,
        "implement": 2,
        "verify": 3,
        "publish": 4,
        "finalize": 5,
    }
    failed_jobs: list[tuple[int, dict[str, Any]]] = []
    for job in jobs:
        stage = _job_stage(str(job.get("name") or ""))
        conclusion = str(job.get("conclusion") or "")
        if (
            stage is not None
            and job.get("status") == "completed"
            and conclusion not in {"success", "skipped", "neutral", ""}
        ):
            failed_jobs.append((stage_order[stage], job))
    if not failed_jobs:
        return None

    _, failed_job = min(failed_jobs, key=lambda item: item[0])
    conclusion = str(failed_job.get("conclusion") or "")
    perm_denials = 0
    error_detail_parts = [
        str(failed_job.get("name") or "").strip().lower(),
        conclusion.lower(),
    ]
    for step in failed_job.get("steps") or []:
        step_name = str(step.get("name") or "").strip().lower()
        step_conclusion = str(step.get("conclusion") or "").strip().lower()
        if step_conclusion != "failure":
            continue
        error_detail_parts.append(f"{step_name} failure")
        if any(
            signal in step_name
            for signal in (
                "tool policy",
                "tool permission",
                "not allowed",
                "permission denied",
                "allowedtools",
                "allowed tools",
            )
        ):
            perm_denials += 1

    log_markers = _job_log_failure_markers(int(failed_job.get("id") or 0))
    error_detail_parts.extend(log_markers)
    if "tool policy" in log_markers:
        perm_denials += 1
    if "error_max_turns" in log_markers:
        conclusion = "error_max_turns"
    if _exact_default_sha() != expected_default_sha:
        raise RuntimeError("Default branch moved during Queue failure classification")
    return conclusion, perm_denials, "; ".join(error_detail_parts), latest_run_id


def _latest_run_failure_evidence(
    issue_number: int,
    fingerprint: str | None = None,
    expected_default_sha: str | None = None,
) -> tuple[str, int, str, int] | None:
    """Return failure evidence bound to the current request and exact default SHA."""
    if expected_default_sha is None:
        expected_default_sha = _exact_default_sha()
    if fingerprint is None:
        issue = runtime.api(f"repos/{runtime.REPO}/issues/{issue_number}")
        timestamp = recovery._request_timestamp(issue)
        if not timestamp:
            return None
        fingerprint = content_bound_request_fingerprint(issue_number, timestamp)
    return _bound_failure_evidence(
        issue_number, fingerprint, expected_default_sha
    )


def _reconcile_existing_attempt_before_checkpoint(
    issue_number: int,
    fingerprint: str,
    attempt: int,
    v2_dispatch: Callable[..., bool],
) -> bool:
    """Consume an already-dispatched exact attempt before WIP suppression."""
    root = hardened._retry_root(issue_number, fingerprint)
    intent_path = f"{root}/retry-{attempt}.json"
    started_path = f"{root}/retry-{attempt}-started.json"
    terminal_path = hardened._terminal_path(issue_number, fingerprint, attempt)
    try:
        intent = hardened._record_payload(intent_path)
        started = hardened._record_payload(started_path)
        terminal = hardened._record_payload(terminal_path)
    except (OSError, RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError):
        return False
    if intent is None or started is not None or terminal is not None:
        return False
    expected_title = hardened._attempt_run_title(issue_number, fingerprint, attempt)
    default_sha = str(intent.get("default_sha") or "")
    if (
        intent.get("issue_number") != issue_number
        or intent.get("attempt") != attempt
        or intent.get("request_fingerprint") != fingerprint
        or intent.get("expected_run_title") != expected_title
        or intent.get("fixed_workflow") != recovery.QUEUE_WORKFLOW_FILE
        or not runtime.EXACT_SHA.fullmatch(default_sha)
        or intent.get("notification") is not False
    ):
        raise RuntimeError("Existing Queue retry intent identity is invalid")
    matches = hardened._matching_dispatch_runs(
        issue_number, fingerprint, attempt, default_sha
    )
    if len(matches) > 1:
        raise RuntimeError("Existing Queue retry identity is ambiguous")
    if not matches:
        return False
    return bool(v2_dispatch(issue_number, fingerprint, attempt))


def _internal_stop_audit(
    issue_number: int,
    fingerprint: str,
    attempt: int,
    failure_class: FailureClass,
    failure_run_id: int,
) -> dict[str, Any]:
    if failure_run_id <= 0:
        raise RuntimeError("Non-retryable stop omitted its Queue run ID")
    default_sha = _exact_default_sha()
    _validated_issue_scope(issue_number)
    request_timestamp = _validated_request_timestamp(issue_number, fingerprint)
    require_no_trusted_alternative(issue_number)
    failure_run = runtime.api(
        f"repos/{runtime.REPO}/actions/runs/{failure_run_id}"
    )
    if not _run_matches_request(
        failure_run,
        issue_number,
        fingerprint,
        default_sha,
        request_timestamp,
    ):
        raise RuntimeError("Non-retryable stop run is not bound to the current request")
    checkpoint = _wip_branch_info(issue_number, strict=True)
    if _exact_default_sha() != default_sha:
        raise RuntimeError("Default branch moved during non-retryable stop audit")
    require_no_trusted_alternative(issue_number)
    status = build_failure_status(
        failure_class=failure_class,
        retry_attempt=attempt - 1,
        max_retries=recovery.MAX_QUEUE_RECOVERY_ATTEMPTS,
        checkpoint_sha=checkpoint[1] if checkpoint else None,
    )
    return {
        "checkpoint_branch": checkpoint[0] if checkpoint else None,
        "checkpoint_sha": checkpoint[1] if checkpoint else None,
        "default_sha": default_sha,
        "failure_class": status.failure_class.value,
        "failure_run_id": failure_run_id,
        "human_action_required": status.human_action_required,
        "issue_number": issue_number,
        "next_automatic_action": status.next_automatic_action,
        "notification": False,
        "reason": "QUEUE_PIPELINE_NON_RETRYABLE_STOP",
        "request_fingerprint": fingerprint,
        "retry_attempt": status.retry_attempt,
    }


def _record_internal_stop(
    issue_number: int,
    fingerprint: str,
    attempt: int,
    failure_class: FailureClass,
    failure_run_id: int,
) -> bool:
    first = _internal_stop_audit(
        issue_number, fingerprint, attempt, failure_class, failure_run_id
    )
    second = _internal_stop_audit(
        issue_number, fingerprint, attempt, failure_class, failure_run_id
    )
    if first != second:
        raise RuntimeError("Non-retryable stop audit changed between live passes")
    path = f"{hardened._retry_root(issue_number, fingerprint)}/internal-stop.json"
    return recovery._put_exact_record(
        path,
        recovery._canonical_record(second),
        f"Record non-retryable Queue stop for Issue #{issue_number}",
    )


def _classified_dispatch_retry(
    issue_number: int,
    fingerprint: str,
    attempt: int,
    v2_dispatch: Callable[..., bool],
) -> bool:
    """Classify the current exact-Issue failure before bounded dispatch or stop."""
    expected_default_sha = _exact_default_sha()
    checkpoint = _wip_branch_info(issue_number, strict=True)
    reconciled = False
    if checkpoint is not None:
        reconciled = _reconcile_existing_attempt_before_checkpoint(
            issue_number, fingerprint, attempt, v2_dispatch
        )
        try:
            evidence = _bound_failure_evidence(
                issue_number, fingerprint, expected_default_sha
            )
        except (OSError, RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError):
            evidence = None
    else:
        evidence = _latest_run_failure_evidence(
            issue_number, fingerprint, expected_default_sha
        )

    failure_class, failure_run_id = _parse_failure_evidence(evidence)
    if not should_auto_retry(
        failure_class, attempt - 1, recovery.MAX_QUEUE_RECOVERY_ATTEMPTS
    ):
        if failure_run_id is not None:
            _record_internal_stop(
                issue_number,
                fingerprint,
                attempt,
                failure_class,
                failure_run_id,
            )
        return reconciled

    if checkpoint is not None:
        return reconciled
    return v2_dispatch(issue_number, fingerprint, attempt)


def main() -> int:
    recovery._request_fingerprint = content_bound_request_fingerprint
    hardened._intent_identity = intent_identity_without_alternative
    hardened._dispatch_fixed_retry = dispatch_without_alternative
    hardened._wait_for_queue_implementation_start = wait_for_admitted_implementation
    hardened._connected_exhaustion_snapshot = complete_connected_exhaustion_snapshot

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

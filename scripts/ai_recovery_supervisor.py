#!/usr/bin/env python3
"""Pure deterministic recovery decision engine.

The engine distinguishes an internal audited stop from a genuine human-only
notification. Routine technical failure never becomes a request for a person to
perform automatable work.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json


class Action(str, Enum):
    NOOP = "NOOP"
    WAIT = "WAIT"
    RETRY_TRANSIENT = "RETRY_TRANSIENT"
    REQUEST_BOUNDED_FIX = "REQUEST_BOUNDED_FIX"
    RERUN_EXACT_SHA_CHECKS = "RERUN_EXACT_SHA_CHECKS"
    REQUEST_CODEX_REVIEW = "REQUEST_CODEX_REVIEW"
    RUN_SELF_RESOLUTION_AUDIT = "RUN_SELF_RESOLUTION_AUDIT"
    INTERNAL_STOP = "INTERNAL_STOP"
    MARK_READY = "MARK_READY"
    MERGE = "MERGE"
    CREATE_NEXT_PHASE = "CREATE_NEXT_PHASE"
    ESCALATE_HUMAN = "ESCALATE_HUMAN"
    BLOCK_AND_CLOSE = "BLOCK_AND_CLOSE"


class Reason(str, Enum):
    DUPLICATE = "DUPLICATE_RECONCILIATION"
    HOLD = "AI_NO_MERGE_HOLD"
    HUMAN_ONLY = "AUDITED_HUMAN_ONLY_ACTION"
    AUDIT_REQUIRED = "SELF_RESOLUTION_AUDIT_REQUIRED"
    HIGH_RISK_INTERNAL_STOP = "HIGH_RISK_INTERNAL_STOP"
    UNTRUSTED_EVIDENCE = "UNTRUSTED_EVIDENCE_INTERNAL_STOP"
    UNAUTHORIZED_PROTECTED = "UNAUTHORIZED_PROTECTED_PATH"
    MISSING_CHECKS = "MISSING_REQUIRED_CHECKS"
    CHECKS_RUNNING = "REQUIRED_CHECKS_RUNNING"
    TRANSIENT = "TRANSIENT_FAILURE_RETRY"
    COOLDOWN = "RETRY_COOLDOWN"
    EXHAUSTED = "RETRY_BUDGET_EXHAUSTED_INTERNAL_STOP"
    BOUNDED_FIX = "BOUNDED_DETERMINISTIC_FIX"
    AMBIGUOUS = "AMBIGUOUS_FAILURE_INTERNAL_STOP"
    CODEX_REQUIRED = "CODEX_REVIEW_REQUIRED"
    CODEX_BLOCKER = "CODEX_BLOCKER"
    NO_PROGRESS = "NO_PROGRESS_INTERNAL_STOP"
    READY = "READY_FOR_REVIEW"
    MERGE = "ALL_GATES_PASSED"
    NEXT_PHASE = "PREDECLARED_NEXT_PHASE"
    NOOP = "NO_ACTION"


@dataclass(frozen=True)
class Check:
    context: str
    state: str
    sha: str
    producer: str
    run_id: str = ""
    failure_fingerprint: str = ""


@dataclass(frozen=True)
class CodexEvidence:
    sha: str = ""
    reviewed: bool = False
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class BoundedFixEvidence:
    sha: str
    run_id: str
    failure_fingerprint: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class NextPhase:
    name: str
    goal: str
    allowed_paths: tuple[str, ...]


@dataclass(frozen=True)
class SelfResolutionAudit:
    """Evidence that connected recovery paths were exhausted before stopping."""

    complete: bool = False
    attempted_connected_paths: tuple[str, ...] = ()
    findings: tuple[str, ...] = ()


@dataclass(frozen=True)
class HumanActionEvidence:
    """Minimal genuine UI-only action and the condition for automatic resumption."""

    category: str = ""
    minimal_ui_action: str = ""
    automatic_resume_condition: str = ""


@dataclass(frozen=True)
class Policy:
    required_checks: tuple[str, ...] = ("CI / validate", "Unit Tests / test")
    trusted_producers: tuple[str, ...] = ("github-actions[bot]",)
    retry_cooldown_seconds: int = 900
    max_retries: int = 3
    max_bounded_fix_paths: int = 4


@dataclass(frozen=True)
class State:
    issue_number: int
    pr_number: int
    head_sha: str
    checks: tuple[Check, ...] = ()
    codex: CodexEvidence = CodexEvidence()
    attempt_count: int = 0
    seconds_since_last_attempt: int | None = None
    last_action_key: str = ""
    transient_failure: bool = False
    deterministic_failure: bool = False
    concrete_failure_run_id: str = ""
    concrete_failure_fingerprint: str = ""
    bounded_fix: BoundedFixEvidence | None = None
    allowed_fix_paths: tuple[str, ...] = ()
    protected_paths_changed: tuple[str, ...] = ()
    protected_authorized_paths: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()
    self_resolution_audit: SelfResolutionAudit = SelfResolutionAudit()
    human_action: HumanActionEvidence = HumanActionEvidence()
    ai_no_merge: bool = False
    draft: bool = True
    mergeable: bool = True
    next_phase: NextPhase | None = None
    no_progress_seconds: int = 0


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: Reason
    explanation: str
    idempotency_key: str


# These are the only conditions that can produce ESCALATE_HUMAN. They describe
# identity/account UI boundaries, not ordinary repository or workflow failures.
HUMAN_ONLY_RISKS = {
    "account-repository-creation-ui",
    "account-repository-connection-ui",
    "credential-mfa",
    "credential-captcha",
    "credential-hardware-key",
    "credential-provider-ui",
    "integration-reconnection-ui",
}

# These risks may prohibit automatic mutation, but stopping is internal. They do
# not authorize telling a person to perform routine engineering work.
INTERNAL_STOP_RISKS = {
    "secret",
    "permission",
    "repository-setting",
    "billing",
    "authentication",
    "deployment",
    "production",
    "destructive-data",
    "essential-ambiguity",
}


def _sorted_dict(value):
    if isinstance(value, dict):
        return {key: _sorted_dict(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple, set)):
        converted = [_sorted_dict(item) for item in value]
        return sorted(converted, key=lambda item: json.dumps(item, sort_keys=True))
    return value


def _key(state: State, policy: Policy, action: Action, reason: Reason) -> str:
    payload = {
        "state": asdict(state),
        "policy": asdict(policy),
        "action": action.value,
        "reason": reason.value,
    }
    payload["state"]["last_action_key"] = ""
    canonical = json.dumps(_sorted_dict(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _decision(
    state: State,
    policy: Policy,
    action: Action,
    reason: Reason,
    explanation: str,
) -> Decision:
    key = _key(state, policy, action, reason)
    if state.last_action_key and state.last_action_key == key:
        return Decision(Action.NOOP, Reason.DUPLICATE, "Identical action was already applied.", key)
    return Decision(action, reason, explanation, key)


def _audit_complete(state: State) -> bool:
    audit = state.self_resolution_audit
    return bool(audit.complete and audit.attempted_connected_paths and audit.findings)


def _audit_required(state: State, policy: Policy, detail: str) -> Decision:
    return _decision(
        state,
        policy,
        Action.RUN_SELF_RESOLUTION_AUDIT,
        Reason.AUDIT_REQUIRED,
        f"Run the bounded connected self-resolution audit before stopping: {detail}",
    )


def _internal_stop(state: State, policy: Policy, reason: Reason, detail: str) -> Decision:
    return _decision(
        state,
        policy,
        Action.INTERNAL_STOP,
        reason,
        f"Internal audited stop for Issue #{state.issue_number}, PR #{state.pr_number}, "
        f"SHA {state.head_sha}: {detail}",
    )


def _human_only_decision(
    state: State, policy: Policy, human_categories: set[str]
) -> Decision:
    evidence = state.human_action
    if not _audit_complete(state):
        return _audit_required(
            state,
            policy,
            "a possible account/credential UI boundary was detected but connected alternatives "
            "have not been conclusively audited",
        )
    if (
        evidence.category not in human_categories
        or not evidence.minimal_ui_action.strip()
        or not evidence.automatic_resume_condition.strip()
    ):
        return _internal_stop(
            state,
            policy,
            Reason.HIGH_RISK_INTERNAL_STOP,
            "human-only evidence is incomplete or does not match the detected fixed category; "
            "no user notification is authorized",
        )
    audit = state.self_resolution_audit
    return _decision(
        state,
        policy,
        Action.ESCALATE_HUMAN,
        Reason.HUMAN_ONLY,
        f"Audited human-only action for Issue #{state.issue_number}, PR #{state.pr_number}, "
        f"SHA {state.head_sha}; category={evidence.category}; "
        f"attempted_connected_paths={', '.join(audit.attempted_connected_paths)}; "
        f"impossibility_evidence={'; '.join(audit.findings)}; "
        f"minimal_ui_action={evidence.minimal_ui_action}; "
        f"automatic_resume_condition={evidence.automatic_resume_condition}",
    )


def decide(state: State, policy: Policy = Policy()) -> Decision:
    if state.ai_no_merge:
        return _decision(state, policy, Action.WAIT, Reason.HOLD, "`ai-no-merge` is a hard hold.")

    risks = set(state.risk_flags)
    human_categories = risks & HUMAN_ONLY_RISKS
    if human_categories:
        return _human_only_decision(state, policy, human_categories)
    if risks & INTERNAL_STOP_RISKS:
        return _internal_stop(
            state,
            policy,
            Reason.HIGH_RISK_INTERNAL_STOP,
            "a protected or high-risk condition forbids automatic mutation; it is not a "
            "human-action notification",
        )

    changed = set(state.protected_paths_changed)
    authorized = set(state.protected_authorized_paths)
    if changed - authorized:
        return _decision(
            state,
            policy,
            Action.BLOCK_AND_CLOSE,
            Reason.UNAUTHORIZED_PROTECTED,
            "Protected paths are not covered by trusted Issue authorization.",
        )

    manifest = {check.context: check for check in state.checks if check.sha == state.head_sha}
    missing = [name for name in policy.required_checks if name not in manifest]
    if missing:
        return _decision(
            state,
            policy,
            Action.RERUN_EXACT_SHA_CHECKS,
            Reason.MISSING_CHECKS,
            f"Required checks are absent for current SHA: {', '.join(missing)}",
        )

    for name in policy.required_checks:
        check = manifest[name]
        if check.producer not in policy.trusted_producers:
            return _internal_stop(
                state,
                policy,
                Reason.UNTRUSTED_EVIDENCE,
                f"check {name} has an untrusted producer and cannot authorize continuation",
            )
        if check.state in {"queued", "pending", "in_progress"}:
            return _decision(
                state,
                policy,
                Action.WAIT,
                Reason.CHECKS_RUNNING,
                f"Check {name} is still running.",
            )

    failures = [
        manifest[name]
        for name in policy.required_checks
        if manifest[name].state != "success"
    ]
    if failures:
        if state.transient_failure:
            if state.attempt_count >= policy.max_retries:
                return _internal_stop(
                    state,
                    policy,
                    Reason.EXHAUSTED,
                    "the bounded transient retry budget is exhausted",
                )
            if state.attempt_count > 0 and state.seconds_since_last_attempt is None:
                return _decision(
                    state,
                    policy,
                    Action.WAIT,
                    Reason.COOLDOWN,
                    "Elapsed-time evidence is missing; cooldown cannot be bypassed.",
                )
            if (state.seconds_since_last_attempt or 0) < policy.retry_cooldown_seconds:
                return _decision(
                    state,
                    policy,
                    Action.WAIT,
                    Reason.COOLDOWN,
                    "Retry cooldown has not elapsed.",
                )
            return _decision(
                state,
                policy,
                Action.RETRY_TRANSIENT,
                Reason.TRANSIENT,
                "Retry one bounded transient failure.",
            )

        if state.deterministic_failure and state.bounded_fix:
            fix = state.bounded_fix
            valid_identity = (
                fix.sha == state.head_sha
                and fix.run_id == state.concrete_failure_run_id
                and fix.failure_fingerprint == state.concrete_failure_fingerprint
            )
            paths = set(fix.paths)
            if (
                valid_identity
                and paths
                and len(paths) <= policy.max_bounded_fix_paths
                and paths <= set(state.allowed_fix_paths)
            ):
                return _decision(
                    state,
                    policy,
                    Action.REQUEST_BOUNDED_FIX,
                    Reason.BOUNDED_FIX,
                    "Concrete current failure is bound to a small allowlisted fix.",
                )
        return _internal_stop(
            state,
            policy,
            Reason.AMBIGUOUS,
            "the failure is not safely repairable within a bounded current scope",
        )

    if not state.codex.reviewed or state.codex.sha != state.head_sha:
        return _decision(
            state,
            policy,
            Action.REQUEST_CODEX_REVIEW,
            Reason.CODEX_REQUIRED,
            "Fresh Codex review is required for the current exact SHA.",
        )
    if state.codex.blockers:
        return _decision(
            state,
            policy,
            Action.REQUEST_BOUNDED_FIX,
            Reason.CODEX_BLOCKER,
            "Codex reported blocking findings on the current exact SHA.",
        )

    if state.no_progress_seconds >= 3600:
        if not _audit_complete(state):
            return _audit_required(
                state,
                policy,
                "no meaningful progress was observed for sixty minutes",
            )
        return _internal_stop(
            state,
            policy,
            Reason.NO_PROGRESS,
            "no meaningful progress remained after the completed bounded self-resolution audit",
        )

    if state.draft:
        return _decision(
            state,
            policy,
            Action.MARK_READY,
            Reason.READY,
            "All evidence gates pass; mark the exact-SHA Draft ready.",
        )
    if state.mergeable:
        return _decision(
            state,
            policy,
            Action.MERGE,
            Reason.MERGE,
            "All exact-SHA gates pass; merge with expected-head-SHA protection.",
        )
    if state.next_phase and state.next_phase.goal and state.next_phase.allowed_paths:
        return _decision(
            state,
            policy,
            Action.CREATE_NEXT_PHASE,
            Reason.NEXT_PHASE,
            "A concrete predeclared next phase is available.",
        )
    return _decision(
        state,
        policy,
        Action.WAIT,
        Reason.NOOP,
        "No safe state transition is available.",
    )

#!/usr/bin/env python3
"""Classify Queue implementation failures and enforce permission-contract preflight.

This module implements two key reliability mechanisms:

Fix C - Contract and permission consistency:
  check_tool_permission_contract() detects when a work order requires commands
  that are not in the configured allowed-tools list.  Call this BEFORE invoking
  the model; a non-empty denial list must abort execution immediately.

Fix D - Automatic recovery classification:
  classify_conclusion() maps raw implementation job conclusions and error
  signatures to a FailureClass.  classify_conclusion() + build_failure_status()
  produce a deterministic FailureStatus record whose human_action_required flag
  explicitly separates automation-owned failures from genuine human-only
  UI/identity failures.

Only three FailureClass values ever set human_action_required=True:
  AUTH_SECRET, and the two human-only credential/integration UI reason codes
  that map to the canonical HUMAN_ONLY_* stop families.

All other failures are automation-owned and may be retried up to max_retries
without any human comment.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FailureClass(str, Enum):
    MAX_TURNS = "max_turns"
    PERMISSION_CONTRACT = "permission_contract"
    AUTH_SECRET = "auth_secret"
    GIT_TRANSPORT = "git_transport"
    TEST_FAILURE = "test_failure"
    PLATFORM_OUTAGE = "platform_outage"
    UNKNOWN = "unknown"


# Automation-owned failures that may be retried without human action.
RETRYABLE_CLASSES: frozenset[FailureClass] = frozenset(
    {
        FailureClass.MAX_TURNS,
        FailureClass.GIT_TRANSPORT,
        FailureClass.PLATFORM_OUTAGE,
        FailureClass.UNKNOWN,
    }
)

# Failures that require a human UI/identity action; never retried automatically.
HUMAN_ONLY_CLASSES: frozenset[FailureClass] = frozenset(
    {
        FailureClass.AUTH_SECRET,
    }
)

# Failures that need a deterministic code or configuration fix before retry.
FIXABLE_CLASSES: frozenset[FailureClass] = frozenset(
    {
        FailureClass.PERMISSION_CONTRACT,
        FailureClass.TEST_FAILURE,
    }
)


@dataclass(frozen=True)
class FailureStatus:
    """Deterministic status record for one failed Queue implementation attempt."""

    failure_class: FailureClass
    retry_attempt: int
    checkpoint_sha: str | None
    checkpoint_artifact: str | None
    next_automatic_action: str
    human_action_required: bool

    def as_status_comment(self) -> str:
        """Return a concise human-readable status block for posting on the Issue."""
        lines = [
            "<!-- foundation-failure-status -->",
            f"- failure_class: `{self.failure_class.value}`",
            f"- retry_attempt: `{self.retry_attempt}`",
        ]
        if self.checkpoint_sha:
            lines.append(f"- checkpoint_sha: `{self.checkpoint_sha}`")
        if self.checkpoint_artifact:
            lines.append(f"- checkpoint_artifact: `{self.checkpoint_artifact}`")
        lines.append(f"- next_automatic_action: {self.next_automatic_action}")
        lines.append(
            f"- human_action_required: `{str(self.human_action_required).lower()}`"
        )
        return "\n".join(lines) + "\n"


def classify_conclusion(
    conclusion: str,
    permission_denials_count: int = 0,
    error_detail: str = "",
) -> FailureClass:
    """Map a raw implementation job conclusion to a FailureClass.

    Arguments:
        conclusion: The raw conclusion string from the action step, e.g.
            "error_max_turns", "failure", "timed_out".
        permission_denials_count: Number of tool-permission denials recorded
            during the implementation turn sequence.
        error_detail: Optional free-text error excerpt for secondary signals.

    Predictable permission denials (conclusion == "failure" with
    permission_denials_count > 0) are classified as PERMISSION_CONTRACT because
    they indicate the task contract, prompt, and tool policy contradict one another
    and must be fixed before the next attempt — not silently retried.
    """
    if conclusion == "error_max_turns":
        return FailureClass.MAX_TURNS

    low = (error_detail or "").lower()

    # Genuine auth/secret failures require human UI action.
    auth_signals = ("401", "403", "token expired", "credential", "secret", "unauthorized")
    if any(sig in low for sig in auth_signals) and "permission" not in low:
        return FailureClass.AUTH_SECRET

    # Predictable permission denials indicate a contract mismatch, not a transient error.
    if permission_denials_count > 0 or "permission denied" in low or "not allowed" in low:
        return FailureClass.PERMISSION_CONTRACT

    if "git" in low and any(sig in low for sig in ("push", "fetch", "clone", "transport")):
        return FailureClass.GIT_TRANSPORT

    if "test" in low and any(sig in low for sig in ("fail", "error", "assert")):
        return FailureClass.TEST_FAILURE

    if conclusion == "timed_out" or any(
        sig in low for sig in ("timeout", "runner", "service unavailable", "503")
    ):
        return FailureClass.PLATFORM_OUTAGE

    return FailureClass.UNKNOWN


def is_human_only_failure(failure_class: FailureClass) -> bool:
    """Return True only for failures that require genuine human UI/identity action.

    This is the sole gate that controls human_action_required in the status record.
    HUMAN_ONLY_CLASSES is intentionally small: only authentication/secret failures
    that cannot be fixed by automation belong here.  Everything else — including
    retry exhaustion, permission contract mismatches, and no-progress stops — is
    automation-owned and must never ask a person to intervene.
    """
    return failure_class in HUMAN_ONLY_CLASSES


def should_auto_retry(
    failure_class: FailureClass,
    retry_attempt: int,
    max_retries: int,
) -> bool:
    """Return True when the bounded retry budget allows one more automatic attempt.

    Human-only and fixable failures are never automatically retried.
    """
    if is_human_only_failure(failure_class):
        return False
    if failure_class in FIXABLE_CLASSES:
        return False
    return retry_attempt < max_retries and failure_class in RETRYABLE_CLASSES


def check_tool_permission_contract(
    required_commands: list[str],
    allowed_bash_commands: list[str],
) -> list[str]:
    """Return required commands that the current tool policy does not permit.

    The caller must abort implementation before invoking the model when this
    function returns a non-empty list.  Counting predictable permission denials
    as productive agent turns is explicitly prohibited; this check prevents them
    by failing fast before the model step.

    Arguments:
        required_commands: Base command names or exact strings that the work
            order requires (e.g. ["python", "pytest", "pip"]).
        allowed_bash_commands: Base command names or exact strings that the
            configured allowed-tools policy permits.

    Returns:
        Denied commands (subset of required_commands not in allowed set).
        An empty list means the contract is consistent.
    """
    allowed: set[str] = set()
    for cmd in allowed_bash_commands:
        normalized = cmd.strip().lower()
        if normalized:
            allowed.add(normalized)
            base = normalized.split()[0]
            allowed.add(base)

    denied: list[str] = []
    for cmd in required_commands:
        normalized = cmd.strip().lower()
        base = normalized.split()[0] if normalized.split() else normalized
        if base not in allowed and normalized not in allowed:
            denied.append(cmd)
    return denied


def build_failure_status(
    failure_class: FailureClass,
    retry_attempt: int,
    max_retries: int,
    checkpoint_sha: str | None = None,
    checkpoint_artifact: str | None = None,
) -> FailureStatus:
    """Build a deterministic FailureStatus from a classified failure.

    The returned record always includes human_action_required so that every
    stopped run explicitly states whether human intervention is needed.
    """
    human_required = is_human_only_failure(failure_class)

    if human_required:
        next_action = "human UI action required; automation paused"
    elif should_auto_retry(failure_class, retry_attempt, max_retries):
        next_action = f"automatic retry {retry_attempt + 1} of {max_retries}"
    elif retry_attempt >= max_retries and failure_class in RETRYABLE_CLASSES:
        next_action = "retry budget exhausted; recording automation incident"
    else:
        next_action = "deterministic fix required; recording automation incident"

    return FailureStatus(
        failure_class=failure_class,
        retry_attempt=retry_attempt,
        checkpoint_sha=checkpoint_sha,
        checkpoint_artifact=checkpoint_artifact,
        next_automatic_action=next_action,
        human_action_required=human_required,
    )

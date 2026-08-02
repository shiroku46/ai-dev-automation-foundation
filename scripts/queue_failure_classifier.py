#!/usr/bin/env python3
"""Classify Queue implementation failures and enforce permission-contract preflight.

This module implements two key reliability mechanisms:

Fix C - Contract and permission consistency:
  check_tool_permission_contract() detects when a work order requires commands
  that are not in the configured allowed-tools list. Call this BEFORE invoking
  the model; a non-empty denial list must abort execution immediately.

Fix D - Automatic recovery classification:
  classify_conclusion() maps raw implementation job conclusions and error
  signatures to a FailureClass. classify_conclusion() + build_failure_status()
  produce a deterministic FailureStatus record whose human_action_required flag
  explicitly separates automation-owned failures from genuine human-only
  UI/identity failures.

Only AUTH_SECRET sets human_action_required=True. Transport, platform, max-turn,
permission-contract, test, and unknown failures remain automation-owned.
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


RETRYABLE_CLASSES: frozenset[FailureClass] = frozenset(
    {
        FailureClass.MAX_TURNS,
        FailureClass.GIT_TRANSPORT,
        FailureClass.PLATFORM_OUTAGE,
        FailureClass.UNKNOWN,
    }
)

HUMAN_ONLY_CLASSES: frozenset[FailureClass] = frozenset({FailureClass.AUTH_SECRET})

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

    Classification order is deliberate. Task-policy denials and Git transport
    failures are automation-owned even when their text contains HTTP 403. Only a
    credential/Secret signal that is not attributable to those contexts may
    become AUTH_SECRET and request human action.
    """
    if conclusion == "error_max_turns":
        return FailureClass.MAX_TURNS

    low = (error_detail or "").lower()

    # Predictable tool-policy denials are a contract mismatch, not an auth problem.
    if permission_denials_count > 0 or "permission denied" in low or "not allowed" in low:
        return FailureClass.PERMISSION_CONTRACT

    # Git/network publication failures stay automation-owned, including CONNECT 403.
    git_context = any(
        signal in low
        for signal in (
            "git push",
            "git fetch",
            "git clone",
            "git transport",
            "connect tunnel",
            "remote ref",
        )
    )
    if git_context or (
        "git" in low
        and any(signal in low for signal in ("push", "fetch", "clone", "transport", "tunnel"))
    ):
        return FailureClass.GIT_TRANSPORT

    # Genuine credential/Secret failures require human UI action.
    auth_signals = (
        "401",
        "403",
        "token expired",
        "expired token",
        "credential expired",
        "missing secret",
        "secret not found",
        "unauthorized",
    )
    if any(signal in low for signal in auth_signals):
        return FailureClass.AUTH_SECRET

    if "test" in low and any(signal in low for signal in ("fail", "error", "assert")):
        return FailureClass.TEST_FAILURE

    if conclusion == "timed_out" or any(
        signal in low for signal in ("timeout", "runner", "service unavailable", "503")
    ):
        return FailureClass.PLATFORM_OUTAGE

    return FailureClass.UNKNOWN


def is_human_only_failure(failure_class: FailureClass) -> bool:
    """Return True only for failures requiring genuine human UI/identity action."""
    return failure_class in HUMAN_ONLY_CLASSES


def should_auto_retry(
    failure_class: FailureClass,
    retry_attempt: int,
    max_retries: int,
) -> bool:
    """Return True when the bounded retry budget allows one more automatic attempt."""
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

    The caller must abort before invoking the model when this returns a non-empty
    list. Matching is case-insensitive and accepts a required base command when
    at least one allowed command uses that base executable.
    """
    allowed: set[str] = set()
    for command in allowed_bash_commands:
        normalized = command.strip().lower()
        if normalized:
            allowed.add(normalized)
            allowed.add(normalized.split()[0])

    denied: list[str] = []
    for command in required_commands:
        normalized = command.strip().lower()
        parts = normalized.split()
        base = parts[0] if parts else normalized
        if base not in allowed and normalized not in allowed:
            denied.append(command)
    return denied


def build_failure_status(
    failure_class: FailureClass,
    retry_attempt: int,
    max_retries: int,
    checkpoint_sha: str | None = None,
    checkpoint_artifact: str | None = None,
) -> FailureStatus:
    """Build a deterministic FailureStatus with explicit human-action semantics."""
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

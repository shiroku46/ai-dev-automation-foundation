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

    Explicit tool-policy evidence takes precedence over a terminal max-turn
    conclusion so deterministic contract repairs are not consumed as transient
    retries. Git transport failures remain automation-owned even when they contain
    HTTP 403. Generic provider permission text remains eligible for auth handling.
    """
    low = (error_detail or "").lower()

    policy_signals = (
        "tool policy",
        "tool permission",
        "allowedtools",
        "allowed tools",
        "not allowed by tool",
        "not permitted by tool",
        "command is not allowed",
        "command not allowed",
    )
    if permission_denials_count > 0 or any(signal in low for signal in policy_signals):
        return FailureClass.PERMISSION_CONTRACT

    if conclusion == "error_max_turns":
        return FailureClass.MAX_TURNS

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

    Matching is case-insensitive and whitespace-normalized. A bare required
    executable is satisfied when any allowed specification uses that executable.
    A required command with arguments must match an allowed command exactly or
    extend an allowed argument-bearing command at a token boundary. A bare
    allowlist entry never authorizes arbitrary subcommands.
    """
    allowed_specs = [
        " ".join(command.strip().lower().split())
        for command in allowed_bash_commands
        if command.strip()
    ]
    allowed_bases = {spec.split()[0] for spec in allowed_specs}

    denied: list[str] = []
    for command in required_commands:
        normalized = " ".join(command.strip().lower().split())
        if not normalized:
            denied.append(command)
            continue

        parts = normalized.split()
        if len(parts) == 1:
            permitted = normalized in allowed_bases
        else:
            permitted = any(
                len(spec.split()) > 1
                and (normalized == spec or normalized.startswith(spec + " "))
                for spec in allowed_specs
            )

        if not permitted:
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

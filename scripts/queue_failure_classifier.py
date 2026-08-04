#!/usr/bin/env python3
"""Classify optional-provider failures and validate edit-only tool contracts.

Provider failure is never a GitHub-direct completion or merge gate. AUTH_SECRET
becomes human-only only when the optional route was explicitly enabled *and* a
separate connected adapter proved that one canonical credential UI action is
unavoidable. Generic errors, quota, setup, account and connection text are not
that proof.
"""
from __future__ import annotations

import re
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
FIXABLE_CLASSES: frozenset[FailureClass] = frozenset(
    {FailureClass.PERMISSION_CONTRACT, FailureClass.TEST_FAILURE}
)
SHELL_CONTROL_PATTERN = re.compile(r"(?:&&|\|\||[;&|`<>]|\$\(|[\r\n])")


@dataclass(frozen=True)
class FailureStatus:
    failure_class: FailureClass
    retry_attempt: int
    checkpoint_sha: str | None
    checkpoint_artifact: str | None
    next_automatic_action: str
    human_action_required: bool

    def as_status_comment(self) -> str:
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
    transport_signals = (
        "connect tunnel",
        "git transport",
        "transport error",
        "could not resolve host",
        "connection reset",
        "connection refused",
        "network is unreachable",
        "remote ref",
    )
    if any(signal in low for signal in transport_signals):
        return FailureClass.GIT_TRANSPORT
    auth_signals = (
        "401",
        "403",
        "token expired",
        "expired token",
        "credential expired",
        "authentication failed",
        "missing secret",
        "secret not found",
        "unauthorized",
    )
    if any(signal in low for signal in auth_signals):
        return FailureClass.AUTH_SECRET
    if any(signal in low for signal in ("git push", "git fetch", "git clone")):
        return FailureClass.GIT_TRANSPORT
    if "test" in low and any(signal in low for signal in ("fail", "error", "assert")):
        return FailureClass.TEST_FAILURE
    if conclusion == "timed_out" or any(
        signal in low for signal in ("timeout", "runner", "service unavailable", "503")
    ):
        return FailureClass.PLATFORM_OUTAGE
    return FailureClass.UNKNOWN


def is_human_only_failure(
    failure_class: FailureClass,
    *,
    optional_provider_explicitly_enabled: bool = False,
    credential_ui_only_proven: bool = False,
) -> bool:
    return (
        failure_class is FailureClass.AUTH_SECRET
        and optional_provider_explicitly_enabled
        and credential_ui_only_proven
    )


def should_auto_retry(
    failure_class: FailureClass,
    retry_attempt: int,
    max_retries: int,
    *,
    optional_provider_explicitly_enabled: bool = False,
    credential_ui_only_proven: bool = False,
) -> bool:
    if is_human_only_failure(
        failure_class,
        optional_provider_explicitly_enabled=optional_provider_explicitly_enabled,
        credential_ui_only_proven=credential_ui_only_proven,
    ):
        return False
    if failure_class in FIXABLE_CLASSES or failure_class is FailureClass.AUTH_SECRET:
        return False
    return retry_attempt < max_retries and failure_class in RETRYABLE_CLASSES


def check_tool_permission_contract(
    required_commands: list[str],
    allowed_bash_commands: list[str],
) -> list[str]:
    """Return commands outside the bounded single-command allowlist."""
    allowed_specs = [
        " ".join(command.strip().lower().split())
        for command in allowed_bash_commands
        if command.strip()
    ]
    allowed_bases = {spec.split()[0] for spec in allowed_specs}
    denied: list[str] = []
    for command in required_commands:
        raw = command.strip()
        if not raw or SHELL_CONTROL_PATTERN.search(raw):
            denied.append(command)
            continue
        normalized = " ".join(raw.lower().split())
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
    *,
    optional_provider_explicitly_enabled: bool = False,
    credential_ui_only_proven: bool = False,
) -> FailureStatus:
    human_required = is_human_only_failure(
        failure_class,
        optional_provider_explicitly_enabled=optional_provider_explicitly_enabled,
        credential_ui_only_proven=credential_ui_only_proven,
    )
    if human_required:
        next_action = "complete the proven optional-provider credential UI action"
    elif failure_class is FailureClass.AUTH_SECRET:
        next_action = "optional provider route unavailable; continue GitHub-direct work"
    elif should_auto_retry(
        failure_class,
        retry_attempt,
        max_retries,
        optional_provider_explicitly_enabled=optional_provider_explicitly_enabled,
        credential_ui_only_proven=credential_ui_only_proven,
    ):
        next_action = f"optional provider retry {retry_attempt + 1} of {max_retries}"
    elif retry_attempt >= max_retries and failure_class in RETRYABLE_CLASSES:
        next_action = "optional retry budget exhausted; continue GitHub-direct work"
    else:
        next_action = "record the optional-route incident; continue GitHub-direct work"
    return FailureStatus(
        failure_class=failure_class,
        retry_attempt=retry_attempt,
        checkpoint_sha=checkpoint_sha,
        checkpoint_artifact=checkpoint_artifact,
        next_automatic_action=next_action,
        human_action_required=human_required,
    )

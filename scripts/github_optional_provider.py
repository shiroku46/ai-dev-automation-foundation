#!/usr/bin/env python3
"""Validate an explicitly optional provider request.

This helper never treats provider availability as a GitHub development gate.
It emits only public-safe route state and never prints credential values.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")


class OptionalProviderError(ValueError):
    pass


def _owner(repository: str, configured: str | None) -> str:
    if "/" not in repository:
        raise OptionalProviderError("repository identity is invalid")
    value = configured or repository.split("/", 1)[0]
    if not LOGIN_RE.fullmatch(value):
        raise OptionalProviderError("automation owner is invalid")
    return value


def evaluate_request(
    event_name: str,
    event: Mapping[str, Any],
    repository: str,
    actor: str,
    configured_owner: str | None,
    credential_available: bool,
) -> dict[str, Any]:
    owner = _owner(repository, configured_owner)
    if actor.casefold() != owner.casefold():
        raise OptionalProviderError("optional provider request is not owner-authorized")

    issue_number: int | None = None
    if event_name == "workflow_dispatch":
        raw = (event.get("inputs") or {}).get("issue_number")
        try:
            issue_number = int(raw)
        except (TypeError, ValueError) as exc:
            raise OptionalProviderError("dispatch issue_number is invalid") from exc
        if issue_number <= 0:
            raise OptionalProviderError("dispatch issue_number is invalid")
    elif event_name == "issue_comment":
        comment = event.get("comment") or {}
        body = comment.get("body")
        issue = event.get("issue") or {}
        if body != "/claude-run":
            raise OptionalProviderError("comment must be the exact standalone /claude-run command")
        if issue.get("pull_request") is not None:
            raise OptionalProviderError("optional provider command must target an Issue")
        issue_number = issue.get("number")
        if isinstance(issue_number, bool) or not isinstance(issue_number, int) or issue_number <= 0:
            raise OptionalProviderError("Issue identity is invalid")
    else:
        raise OptionalProviderError("unsupported optional provider event")

    if not credential_available:
        return {
            "invoke": False,
            "issue_number": issue_number,
            "implementation_route": "claude-optional",
            "provider_state": "route-unavailable",
            "next_action": "Continue through GitHub-direct implementation",
            "human_action_required": False,
        }
    return {
        "invoke": True,
        "issue_number": issue_number,
        "implementation_route": "claude-optional",
        "provider_state": "available",
        "next_action": "Run the explicitly requested optional provider task",
        "human_action_required": False,
    }


def _write_output(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with Path(output_path).open("a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    try:
        event_path = Path(os.environ.get("GITHUB_EVENT_PATH", ""))
        event = json.loads(event_path.read_text(encoding="utf-8"))
        result = evaluate_request(
            os.environ.get("GITHUB_EVENT_NAME", ""),
            event,
            os.environ.get("GITHUB_REPOSITORY", ""),
            os.environ.get("GITHUB_ACTOR", ""),
            os.environ.get("AUTOMATION_OWNER") or None,
            os.environ.get("OPTIONAL_PROVIDER_CREDENTIAL_AVAILABLE") == "true",
        )
        _write_output("invoke", "true" if result["invoke"] else "false")
        _write_output("issue_number", str(result["issue_number"]))
        _write_output("provider_state", result["provider_state"])
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OptionalProviderError, OSError, json.JSONDecodeError) as exc:
        print(f"github-optional-provider: denied: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

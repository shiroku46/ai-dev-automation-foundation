#!/usr/bin/env python3
"""Pure exact-head validation for trusted external CI check runs."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$")
MAX_REQUIREMENTS = 20
MAX_CHECK_RUNS = 5000


class ExternalValidationError(ValueError):
    """External validation configuration or evidence is malformed."""


@dataclass(frozen=True, order=True)
class ExternalCheckRequirement:
    provider: str
    name: str
    app_slug: str
    app_id: int | None = None


@dataclass(frozen=True, order=True)
class ExternalCheckEvidence:
    provider: str
    name: str
    app_slug: str
    app_id: int | None
    state: str
    run_id: int


def _text(value: Any, *, label: str, limit: int = 200) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ExternalValidationError(f"{label} is invalid")
    if value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ExternalValidationError(f"{label} is invalid")
    return value


def _positive_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ExternalValidationError(f"{label} is invalid")
    return value


def validate_requirements(
    requirements: Sequence[ExternalCheckRequirement],
) -> tuple[ExternalCheckRequirement, ...]:
    if (
        not isinstance(requirements, Sequence)
        or isinstance(requirements, (str, bytes))
        or not requirements
        or len(requirements) > MAX_REQUIREMENTS
    ):
        raise ExternalValidationError("external check requirements are invalid")

    normalized: list[ExternalCheckRequirement] = []
    identities: set[tuple[str, str]] = set()
    for requirement in requirements:
        if not isinstance(requirement, ExternalCheckRequirement):
            raise ExternalValidationError("external check requirement is invalid")
        provider = _text(requirement.provider, label="provider", limit=80)
        name = _text(requirement.name, label="check name")
        app_slug = _text(requirement.app_slug, label="app slug", limit=100).casefold()
        if SLUG_RE.fullmatch(app_slug) is None:
            raise ExternalValidationError("app slug is invalid")
        app_id = requirement.app_id
        if app_id is not None:
            app_id = _positive_int(app_id, label="app id")
        identity = (provider.casefold(), name.casefold())
        if identity in identities:
            raise ExternalValidationError("duplicate external check identity")
        identities.add(identity)
        normalized.append(
            ExternalCheckRequirement(provider, name, app_slug, app_id)
        )
    return tuple(sorted(normalized))


def _associated_with_pr(run: Mapping[str, Any], pr_number: int) -> bool:
    pulls = run.get("pull_requests")
    return isinstance(pulls, list) and any(
        isinstance(item, Mapping)
        and not isinstance(item.get("number"), bool)
        and item.get("number") == pr_number
        for item in pulls
    )


def _evidence_state(run: Mapping[str, Any]) -> str:
    if run.get("status") != "completed":
        return "pending"
    return "success" if run.get("conclusion") == "success" else "failure"


def snapshot_external_checks(
    check_runs: Sequence[Mapping[str, Any]],
    *,
    head_sha: str,
    pr_number: int,
    requirements: Sequence[ExternalCheckRequirement],
) -> tuple[ExternalCheckEvidence, ...]:
    """Return one deterministic latest evidence record per trusted requirement.

    Only exact-head, exact-PR, exact-app, exact-name evidence participates.
    A newer pending/failing run supersedes an older success for the same identity.
    """
    if not isinstance(head_sha, str) or SHA_RE.fullmatch(head_sha) is None:
        raise ExternalValidationError("head SHA is invalid")
    pr_number = _positive_int(pr_number, label="PR number")
    required = validate_requirements(requirements)
    if (
        not isinstance(check_runs, Sequence)
        or isinstance(check_runs, (str, bytes))
        or len(check_runs) > MAX_CHECK_RUNS
    ):
        raise ExternalValidationError("external check evidence collection is invalid")

    result: list[ExternalCheckEvidence] = []
    for requirement in required:
        matches: list[tuple[int, Mapping[str, Any], int | None]] = []
        for run in check_runs:
            if not isinstance(run, Mapping):
                continue
            if run.get("head_sha") != head_sha or run.get("name") != requirement.name:
                continue
            app = run.get("app")
            if not isinstance(app, Mapping):
                continue
            slug = str(app.get("slug") or "").casefold()
            if slug != requirement.app_slug:
                continue
            raw_app_id = app.get("id")
            if raw_app_id is not None and (
                isinstance(raw_app_id, bool) or not isinstance(raw_app_id, int) or raw_app_id <= 0
            ):
                raise ExternalValidationError("check run app id is malformed")
            if requirement.app_id is not None and raw_app_id != requirement.app_id:
                continue
            if not _associated_with_pr(run, pr_number):
                continue
            run_id = _positive_int(run.get("id"), label="check run id")
            matches.append((run_id, run, raw_app_id))

        if not matches:
            result.append(
                ExternalCheckEvidence(
                    requirement.provider,
                    requirement.name,
                    requirement.app_slug,
                    requirement.app_id,
                    "missing",
                    0,
                )
            )
            continue

        # GitHub check-run IDs are immutable monotonically allocated identities.
        # The highest matching ID is the latest exact identity; stale success cannot
        # mask a later pending/failing rerun.
        run_id, selected, observed_app_id = max(matches, key=lambda item: item[0])
        result.append(
            ExternalCheckEvidence(
                requirement.provider,
                requirement.name,
                requirement.app_slug,
                requirement.app_id if requirement.app_id is not None else observed_app_id,
                _evidence_state(selected),
                run_id,
            )
        )

    return tuple(sorted(result))


def external_checks_satisfied(evidence: Sequence[ExternalCheckEvidence]) -> bool:
    if not evidence:
        return False
    return all(
        isinstance(item, ExternalCheckEvidence) and item.state == "success"
        for item in evidence
    )

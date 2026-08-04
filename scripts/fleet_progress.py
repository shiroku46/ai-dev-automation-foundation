#!/usr/bin/env python3
"""Validate GitHub-only fleet progress records and render deterministic Markdown.

This command is deliberately offline. It reads one bounded JSON document,
performs no network or GitHub operation, and writes only to stdout or an
explicitly supplied output path.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = 2
MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_PROJECTS = 500
MAX_CHECKS_PER_PROJECT = 100

STATUS_VALUES = frozenset(
    {
        "backlog",
        "ready",
        "implementing",
        "pr_open",
        "ci_running",
        "review_required",
        "fix_required",
        "human_action",
        "blocked",
        "ready_to_merge",
        "completed",
        "idle",
    }
)
IMPLEMENTATION_ROUTES = frozenset(
    {"github-direct", "codex-optional", "claude-optional"}
)
RISK_TIERS = frozenset({"low", "standard", "protected"})
REVIEW_ROUTES = frozenset({"github-coordinator"})
REVIEW_STATES = frozenset({"required", "pending", "clean", "blocked"})
CHECK_STATES = frozenset(
    {
        "queued",
        "in_progress",
        "success",
        "failure",
        "cancelled",
        "skipped",
        "neutral",
        "timed_out",
        "action_required",
        "stale",
        "missing",
    }
)
PASSING_CHECK_STATES = frozenset({"success", "skipped", "neutral"})
HEAD_REQUIRED_STATUSES = frozenset(
    {"pr_open", "ci_running", "review_required", "fix_required", "ready_to_merge"}
)
BLOCKER_REQUIRED_STATUSES = frozenset({"fix_required", "human_action", "blocked"})
NO_BLOCKER_STATUSES = frozenset({"ready_to_merge", "completed", "idle"})
ACTIVE_STATUSES = frozenset(
    {"backlog", "ready", "implementing", "pr_open", "ci_running", "review_required"}
)

REPOSITORY_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,98}[A-Za-z0-9])?/"
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,98}[A-Za-z0-9])?$"
)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
CHECK_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _./:+()\-]{0,119}$")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

SECTIONS = (
    "Human Action Required",
    "Blocked",
    "Active Implementation and Review",
    "Ready to Merge",
    "Completed or Idle",
)


class FleetProgressError(ValueError):
    """Raised when fleet progress input fails the bounded contract."""


@dataclass(frozen=True)
class ProjectStatus:
    repository: str
    phase: str
    issue: int | None
    pull_request: int | None
    status: str
    head_sha: str | None
    checks: tuple[tuple[str, str], ...]
    implementation_route: str
    risk_tier: str
    review_route: str
    review_state: str
    next_action: str
    blocker: str | None
    human_action_required: bool
    updated_at: str


@dataclass(frozen=True)
class FleetProgress:
    generated_at: str
    projects: tuple[ProjectStatus, ...]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FleetProgressError(f"input contains duplicate object key: {key}")
        result[key] = value
    return result


def _expect_object(value: Any, location: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise FleetProgressError(f"{location} must be an object")
    return value


def _expect_exact_keys(
    value: Mapping[str, Any], required: frozenset[str], location: str
) -> None:
    missing = sorted(required - value.keys())
    extra = sorted(value.keys() - required)
    if missing:
        raise FleetProgressError(f"{location} is missing fields: {', '.join(missing)}")
    if extra:
        raise FleetProgressError(f"{location} has unsupported fields: {', '.join(extra)}")


def _expect_text(value: Any, location: str, *, max_length: int = 500) -> str:
    if not isinstance(value, str):
        raise FleetProgressError(f"{location} must be a string")
    text = value.strip()
    if not text:
        raise FleetProgressError(f"{location} must not be empty")
    if len(text) > max_length:
        raise FleetProgressError(f"{location} exceeds {max_length} characters")
    if CONTROL_RE.search(text):
        raise FleetProgressError(f"{location} contains a control character")
    return text


def _expect_optional_text(value: Any, location: str) -> str | None:
    if value is None:
        return None
    return _expect_text(value, location)


def _expect_optional_positive_int(value: Any, location: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise FleetProgressError(f"{location} must be a positive integer or null")
    return value


def _expect_enum(value: Any, allowed: frozenset[str], location: str) -> str:
    text = _expect_text(value, location, max_length=80)
    if text not in allowed:
        raise FleetProgressError(
            f"{location} must be one of: {', '.join(sorted(allowed))}"
        )
    return text


def _expect_utc_timestamp(value: Any, location: str) -> str:
    text = _expect_text(value, location, max_length=40)
    if not text.endswith("Z"):
        raise FleetProgressError(f"{location} must be an ISO-8601 UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise FleetProgressError(f"{location} must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise FleetProgressError(f"{location} must use UTC")
    return text


def _validate_checks(value: Any, location: str) -> tuple[tuple[str, str], ...]:
    checks = _expect_object(value, location)
    if len(checks) > MAX_CHECKS_PER_PROJECT:
        raise FleetProgressError(
            f"{location} exceeds {MAX_CHECKS_PER_PROJECT} check records"
        )
    normalized: list[tuple[str, str]] = []
    for name, conclusion in checks.items():
        if not isinstance(name, str) or not CHECK_NAME_RE.fullmatch(name):
            raise FleetProgressError(f"{location} contains an invalid check name")
        normalized.append(
            (name, _expect_enum(conclusion, CHECK_STATES, f"{location}.{name}"))
        )
    normalized.sort(key=lambda item: (item[0].lower(), item[0]))
    return tuple(normalized)


def _validate_relationships(project: ProjectStatus, location: str) -> None:
    if project.status in HEAD_REQUIRED_STATUSES and project.head_sha is None:
        raise FleetProgressError(
            f"{location}.head_sha is required for status {project.status}"
        )
    if project.review_state in {"pending", "clean", "blocked"} and project.head_sha is None:
        raise FleetProgressError(
            f"{location}.head_sha is required for review state {project.review_state}"
        )
    if project.status in BLOCKER_REQUIRED_STATUSES and project.blocker is None:
        raise FleetProgressError(
            f"{location}.blocker is required for status {project.status}"
        )
    if project.review_state == "blocked" and project.blocker is None:
        raise FleetProgressError(
            f"{location}.blocker is required when review_state is blocked"
        )
    if project.status in NO_BLOCKER_STATUSES and project.blocker is not None:
        raise FleetProgressError(
            f"{location}.blocker must be null for status {project.status}"
        )
    if project.human_action_required != (project.status == "human_action"):
        raise FleetProgressError(
            f"{location}.human_action_required must be true exactly for human_action status"
        )
    if project.review_route != "github-coordinator":
        raise FleetProgressError(
            f"{location}.review_route must be github-coordinator"
        )
    if project.status == "review_required" and project.review_state not in {
        "required",
        "pending",
        "blocked",
    }:
        raise FleetProgressError(
            f"{location}.review_state is inconsistent with review_required status"
        )
    if project.status == "ready_to_merge":
        if not project.checks or any(
            state not in PASSING_CHECK_STATES for _, state in project.checks
        ):
            raise FleetProgressError(
                f"{location}.checks must all pass before ready_to_merge"
            )
        if project.review_state != "clean":
            raise FleetProgressError(
                f"{location}.review_state must be clean before ready_to_merge"
            )
        if project.blocker is not None or project.human_action_required:
            raise FleetProgressError(
                f"{location} cannot be ready_to_merge while blocked or human-owned"
            )


def _validate_project(value: Any, index: int) -> ProjectStatus:
    location = f"projects[{index}]"
    project = _expect_object(value, location)
    required = frozenset(
        {
            "repository",
            "phase",
            "issue",
            "pull_request",
            "status",
            "head_sha",
            "checks",
            "implementation_route",
            "risk_tier",
            "review_route",
            "review_state",
            "next_action",
            "blocker",
            "human_action_required",
            "updated_at",
        }
    )
    _expect_exact_keys(project, required, location)

    repository = _expect_text(project["repository"], f"{location}.repository", max_length=200)
    if not REPOSITORY_RE.fullmatch(repository):
        raise FleetProgressError(
            f"{location}.repository must use a bounded owner/name form"
        )
    head_value = project["head_sha"]
    head_sha: str | None
    if head_value is None:
        head_sha = None
    else:
        head_sha = _expect_text(head_value, f"{location}.head_sha", max_length=40)
        if not SHA_RE.fullmatch(head_sha):
            raise FleetProgressError(
                f"{location}.head_sha must be a lowercase 40-character SHA"
            )

    normalized = ProjectStatus(
        repository=repository,
        phase=_expect_text(project["phase"], f"{location}.phase", max_length=160),
        issue=_expect_optional_positive_int(project["issue"], f"{location}.issue"),
        pull_request=_expect_optional_positive_int(
            project["pull_request"], f"{location}.pull_request"
        ),
        status=_expect_enum(project["status"], STATUS_VALUES, f"{location}.status"),
        head_sha=head_sha,
        checks=_validate_checks(project["checks"], f"{location}.checks"),
        implementation_route=_expect_enum(
            project["implementation_route"],
            IMPLEMENTATION_ROUTES,
            f"{location}.implementation_route",
        ),
        risk_tier=_expect_enum(
            project["risk_tier"], RISK_TIERS, f"{location}.risk_tier"
        ),
        review_route=_expect_enum(
            project["review_route"], REVIEW_ROUTES, f"{location}.review_route"
        ),
        review_state=_expect_enum(
            project["review_state"], REVIEW_STATES, f"{location}.review_state"
        ),
        next_action=_expect_text(
            project["next_action"], f"{location}.next_action", max_length=500
        ),
        blocker=_expect_optional_text(project["blocker"], f"{location}.blocker"),
        human_action_required=project["human_action_required"],
        updated_at=_expect_utc_timestamp(
            project["updated_at"], f"{location}.updated_at"
        ),
    )
    if not isinstance(normalized.human_action_required, bool):
        raise FleetProgressError(f"{location}.human_action_required must be a boolean")
    _validate_relationships(normalized, location)
    return normalized


def validate_document(value: Any) -> FleetProgress:
    document = _expect_object(value, "document")
    _expect_exact_keys(
        document,
        frozenset({"schema_version", "generated_at", "projects"}),
        "document",
    )
    if isinstance(document["schema_version"], bool) or document["schema_version"] != SCHEMA_VERSION:
        raise FleetProgressError(
            f"document.schema_version must equal {SCHEMA_VERSION}; legacy external-auditor schemas are unsupported"
        )
    generated_at = _expect_utc_timestamp(document["generated_at"], "document.generated_at")
    raw_projects = document["projects"]
    if not isinstance(raw_projects, list):
        raise FleetProgressError("document.projects must be an array")
    if len(raw_projects) > MAX_PROJECTS:
        raise FleetProgressError(f"document.projects exceeds {MAX_PROJECTS} records")

    projects = tuple(
        _validate_project(project, index) for index, project in enumerate(raw_projects)
    )
    repositories: set[str] = set()
    for project in projects:
        key = project.repository.lower()
        if key in repositories:
            raise FleetProgressError(
                f"document.projects contains duplicate repository {project.repository}"
            )
        repositories.add(key)
    return FleetProgress(generated_at=generated_at, projects=projects)


def load_document(path: Path) -> FleetProgress:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise FleetProgressError(
            f"cannot inspect input file: {exc.strerror or 'I/O error'}"
        ) from exc
    if size > MAX_INPUT_BYTES:
        raise FleetProgressError(f"input exceeds {MAX_INPUT_BYTES} bytes")
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        detail = getattr(exc, "strerror", None) or "invalid UTF-8 or I/O error"
        raise FleetProgressError(f"cannot read input file: {detail}") from exc
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except FleetProgressError:
        raise
    except json.JSONDecodeError as exc:
        raise FleetProgressError(
            f"input is not valid JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc
    return validate_document(value)


def _section_for(project: ProjectStatus) -> str:
    if project.human_action_required:
        return "Human Action Required"
    if project.status in {"blocked", "fix_required"} or project.review_state == "blocked":
        return "Blocked"
    if project.status == "ready_to_merge":
        return "Ready to Merge"
    if project.status in {"completed", "idle"}:
        return "Completed or Idle"
    if project.status in ACTIVE_STATUSES:
        return "Active Implementation and Review"
    raise FleetProgressError(f"unsupported status classification: {project.status}")


def _sort_key(project: ProjectStatus) -> tuple[str, int, int]:
    return (
        project.repository.lower(),
        project.issue if project.issue is not None else 2**31,
        project.pull_request if project.pull_request is not None else 2**31,
    )


def _escape_cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|")


def _work_text(project: ProjectStatus) -> str:
    parts: list[str] = []
    if project.issue is not None:
        parts.append(f"Issue #{project.issue}")
    if project.pull_request is not None:
        parts.append(f"PR #{project.pull_request}")
    return " / ".join(parts) if parts else "—"


def _check_text(project: ProjectStatus) -> str:
    if not project.checks:
        return "—"
    passing = sum(state in PASSING_CHECK_STATES for _, state in project.checks)
    non_passing = [
        f"{name}={state}"
        for name, state in project.checks
        if state not in PASSING_CHECK_STATES
    ]
    summary = f"{passing}/{len(project.checks)} passing"
    if non_passing:
        summary += "; " + ", ".join(non_passing)
    return summary


def render_markdown(progress: FleetProgress) -> str:
    grouped: dict[str, list[ProjectStatus]] = {section: [] for section in SECTIONS}
    for project in sorted(progress.projects, key=_sort_key):
        grouped[_section_for(project)].append(project)

    lines = [
        "# Fleet Progress Dashboard",
        "",
        f"Generated from validated schema-version-{SCHEMA_VERSION} records at `{progress.generated_at}`.",
        "",
        "## Summary",
        "",
        "| Category | Projects |",
        "|---|---:|",
    ]
    for section in SECTIONS:
        lines.append(f"| {_escape_cell(section)} | {len(grouped[section])} |")
    lines.extend(["", f"Total projects: **{len(progress.projects)}**.", ""])

    for section in SECTIONS:
        lines.extend(
            [
                f"## {section}",
                "",
                "| Repository | Phase | Work | Status | SHA | Checks | Route / Risk | Review | Next action | Blocker | Updated |",
                "|---|---|---|---|---|---|---|---|---|---|---|",
            ]
        )
        if not grouped[section]:
            lines.append("| — | — | — | — | — | — | — | — | — | — | — |")
        else:
            for project in grouped[section]:
                values = (
                    project.repository,
                    project.phase,
                    _work_text(project),
                    project.status,
                    project.head_sha[:12] if project.head_sha else "—",
                    _check_text(project),
                    f"{project.implementation_route} / {project.risk_tier}",
                    f"{project.review_route} / {project.review_state}",
                    project.next_action,
                    project.blocker or "—",
                    project.updated_at,
                )
                lines.append("| " + " | ".join(_escape_cell(value) for value in values) + " |")
        lines.append("")

    lines.extend(
        [
            "The dashboard is a projection of its JSON source. GitHub Issues, Pull Requests, exact-head checks, coordinator-review records, and exact remote SHAs remain authoritative.",
            "",
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate fleet progress JSON and render a Markdown dashboard."
    )
    parser.add_argument("input", type=Path, help="Path to the fleet progress JSON file")
    parser.add_argument("--output", type=Path, help="Explicit Markdown output path")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate only; do not render or write an output file",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.check and args.output is not None:
        parser.error("--check cannot be combined with --output")
    try:
        progress = load_document(args.input)
        if args.check:
            print(f"valid: {len(progress.projects)} project records")
            return 0
        markdown = render_markdown(progress)
        if args.output is None:
            sys.stdout.write(markdown)
        else:
            args.output.write_text(markdown, encoding="utf-8")
        return 0
    except FleetProgressError as exc:
        print(f"fleet-progress: error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(
            f"fleet-progress: error: cannot write output file: {exc.strerror or 'I/O error'}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

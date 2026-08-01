#!/usr/bin/env python3
"""Practical scope and risk helpers shared by trusted runtime and tests."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import re

PROTECTED_PREFIXES = (".github/", "bootstrap/")
PROTECTED_EXACT = {
    "SECURITY.md",
    "AGENTS.md",
    "CLAUDE.md",
    "INSTALL_CHECKLIST.md",
    "docs/MINIMUM_SAFETY_PROFILE.md",
    "docs/OPERATING_RULES.md",
    "docs/PUBLIC_SECURITY_MODEL.md",
    "scripts/public_export_guard.py",
    "scripts/validate_repository.py",
    "scripts/supervisor_final_guard.py",
    "scripts/supervisor_policy.py",
    "scripts/supervisor_runtime.py",
    "scripts/supervisor_queue_recovery.py",
    "scripts/supervisor_queue_recovery_v2.py",
    "scripts/supervisor_queue_recovery_v3.py",
    "scripts/ai_recovery_supervisor.py",
}
LOW_RISK_PREFIXES = ("docs/", "tests/")
LOW_RISK_EXACT = {
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "FOUNDATION.lock.json",
}
LOW_RISK_SUFFIXES = (".md", ".rst", ".txt")
ALLOWED_SCOPE_HEADINGS = frozenset(
    {
        "allowed paths",
        "allowed scope",
        "exact scope",
        "exact authorized scope",
    }
)
TASK_SCOPE_MARKER = "<!-- foundation-task-scope"
TASK_SCOPE_END = "-->"
RISK_LEVELS = frozenset({"low", "standard", "protected"})


@dataclass(frozen=True)
class TaskScope:
    risk: str
    paths: tuple[str, ...]
    operation: str
    prohibited: str
    checks: tuple[str, ...]
    legacy: bool = False


def normalize_path(path: str) -> str:
    normalized = str(PurePosixPath(path.strip().strip("`")))
    if normalized in {"", "."} or normalized.startswith("../") or normalized.startswith("/"):
        raise ValueError("authorized paths must be repository-relative")
    return normalized


def is_protected(path: str) -> bool:
    normalized = normalize_path(path)
    return normalized in PROTECTED_EXACT or normalized.startswith(PROTECTED_PREFIXES)


def _pattern_base(pattern: str) -> str:
    normalized = normalize_path(pattern)
    return normalized[:-3].rstrip("/") if normalized.endswith("/**") else normalized


def _pattern_is_protected(pattern: str) -> bool:
    normalized = normalize_path(pattern)
    base = _pattern_base(normalized)
    if base in {".github", "bootstrap"}:
        return True
    if is_protected(base):
        return True
    if normalized.endswith("/**"):
        return any(
            protected == base or protected.startswith(f"{base}/")
            for protected in PROTECTED_EXACT
        )
    return False


def _pattern_is_low_risk(pattern: str) -> bool:
    normalized = normalize_path(pattern)
    if _pattern_is_protected(normalized):
        return False
    base = _pattern_base(normalized)
    if base in {"docs", "tests"}:
        return True
    if normalized in LOW_RISK_EXACT:
        return True
    if normalized.startswith(LOW_RISK_PREFIXES):
        return True
    return normalized.endswith(LOW_RISK_SUFFIXES)


def _outside_fenced_code(content: str) -> str:
    """Remove Markdown fenced-code examples before parsing authorization blocks."""
    output: list[str] = []
    fence_character: str | None = None
    fence_length = 0
    for raw in content.splitlines():
        stripped = raw.lstrip()
        fence = re.match(r"^(`{3,}|~{3,})", stripped)
        if fence_character is None:
            if fence:
                token = fence.group(1)
                fence_character = token[0]
                fence_length = len(token)
                continue
            output.append(raw)
            continue
        if re.match(rf"^{re.escape(fence_character)}{{{fence_length},}}\s*$", stripped):
            fence_character = None
            fence_length = 0
    return "\n".join(output)


def parse_issue_number(pr_body: str) -> int | None:
    match = re.search(r"(?im)\b(?:closes|fixes|resolves)\s+#(\d+)\b", pr_body or "")
    return int(match.group(1)) if match else None


def _bullet_path(raw: str) -> str | None:
    line = raw.strip()
    if not line.startswith("- "):
        return None
    value = line[2:].strip()
    if value.startswith("`") and "`" in value[1:]:
        value = value[1:].split("`", 1)[0]
    elif any(character.isspace() for character in value):
        return None
    try:
        return normalize_path(value)
    except ValueError:
        return None


def _unique_block_value(lines: list[str], key: str) -> str:
    prefix = f"{key}:"
    values = [
        line.strip()[len(prefix) :].strip()
        for line in lines
        if line.strip().startswith(prefix)
    ]
    if len(values) != 1:
        raise ValueError(f"foundation-task-scope requires exactly one {key} declaration")
    return values[0]


def parse_task_scope(issue_body: str) -> TaskScope | None:
    body = _outside_fenced_code(issue_body or "")
    marker_count = body.count(TASK_SCOPE_MARKER)
    if marker_count == 0:
        return None
    if marker_count != 1:
        raise ValueError("exactly one foundation-task-scope block is required")
    remainder = body.split(TASK_SCOPE_MARKER, 1)[1]
    if TASK_SCOPE_END not in remainder:
        raise ValueError("foundation-task-scope block is not terminated")
    block, _ = remainder.split(TASK_SCOPE_END, 1)
    lines = block.splitlines()
    if sum(1 for line in lines if line.strip() == "paths:") != 1:
        raise ValueError("foundation-task-scope requires exactly one paths section")
    if sum(1 for line in lines if line.strip() == "checks:") != 1:
        raise ValueError("foundation-task-scope requires exactly one checks section")
    risk = _unique_block_value(lines, "risk").lower()
    operation = _unique_block_value(lines, "operation")
    prohibited = _unique_block_value(lines, "prohibited")
    if risk not in RISK_LEVELS:
        raise ValueError("foundation-task-scope risk must be low, standard, or protected")
    if not operation:
        raise ValueError("foundation-task-scope operation is required")
    if not prohibited:
        raise ValueError("foundation-task-scope prohibited statement is required")

    paths: list[str] = []
    checks: list[str] = []
    active: str | None = None
    for raw in lines:
        line = raw.strip()
        if line == "paths:":
            active = "paths"
            continue
        if line == "checks:":
            active = "checks"
            continue
        if re.match(r"^[a-z_]+:", line):
            active = None
            continue
        if active == "paths":
            path = _bullet_path(raw)
            if path:
                paths.append(path)
        elif active == "checks" and line.startswith("- "):
            check = line[2:].strip().strip("`")
            if check:
                checks.append(check)
    if not paths:
        raise ValueError("foundation-task-scope paths are required")
    if len(paths) != len(set(paths)):
        raise ValueError("foundation-task-scope paths must be unique")
    if not checks:
        raise ValueError("foundation-task-scope checks are required")
    if len(checks) != len(set(checks)):
        raise ValueError("foundation-task-scope checks must be unique")
    if risk != "protected" and any(_pattern_is_protected(path) for path in paths):
        raise ValueError("protected paths require risk: protected")
    if risk == "low" and any(not _pattern_is_low_risk(path) for path in paths):
        raise ValueError("low-risk paths must be documentation, tests, or generated metadata")
    return TaskScope(
        risk=risk,
        paths=tuple(paths),
        operation=operation,
        prohibited=prohibited,
        checks=tuple(checks),
    )


def protected_authorized_paths(issue_body: str) -> set[str]:
    scope = parse_task_scope(issue_body)
    if scope is not None:
        return set(scope.paths) if scope.risk == "protected" else set()
    body = _outside_fenced_code(issue_body or "")
    marker = "<!-- foundation-protected-authorization"
    end = "-->"
    if marker not in body:
        return set()
    block = body.split(marker, 1)[1].split(end, 1)[0]
    paths: set[str] = set()
    in_paths = False
    for raw in block.splitlines():
        line = raw.strip()
        if line == "paths:":
            in_paths = True
            continue
        if in_paths:
            path = _bullet_path(raw)
            if path:
                paths.add(path)
                continue
            if line and not line.startswith("#"):
                in_paths = False
    return paths


def _legacy_declared_paths(issue_body: str) -> set[str]:
    paths: set[str] = set()
    in_scope = False
    for raw in _outside_fenced_code(issue_body or "").splitlines():
        line = raw.strip()
        heading = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if heading:
            in_scope = heading.group(1).strip().lower() in ALLOWED_SCOPE_HEADINGS
            continue
        if in_scope and line.startswith("<!--"):
            in_scope = False
            continue
        if in_scope:
            path = _bullet_path(raw)
            if path:
                paths.add(path)
    return paths


def declared_paths(issue_body: str) -> set[str]:
    scope = parse_task_scope(issue_body)
    if scope is not None:
        return set(scope.paths)
    return _legacy_declared_paths(issue_body)


def _matches(path: str, pattern: str) -> bool:
    normalized = normalize_path(path)
    authorized = normalize_path(pattern)
    if authorized.endswith("/**"):
        prefix = authorized[:-3].rstrip("/")
        return bool(prefix) and (
            normalized == prefix or normalized.startswith(f"{prefix}/")
        )
    if any(character in authorized for character in "*?["):
        return False
    return normalized == authorized


def scope_is_authorized(changed_paths, issue_body: str) -> bool:
    patterns = declared_paths(issue_body)
    changed = {normalize_path(path) for path in changed_paths}
    return bool(changed) and bool(patterns) and all(
        any(_matches(path, pattern) for pattern in patterns) for path in changed
    )


def protected_scope_is_authorized(changed_paths, issue_body: str) -> bool:
    scope = parse_task_scope(issue_body)
    protected = {normalize_path(path) for path in changed_paths if is_protected(path)}
    if not protected:
        return True
    if scope is not None:
        return scope.risk == "protected" and all(
            any(_matches(path, pattern) for pattern in scope.paths) for path in protected
        )
    patterns = protected_authorized_paths(issue_body)
    return all(any(_matches(path, pattern) for pattern in patterns) for path in protected)


def risk_for_changes(changed_paths, issue_body: str) -> str:
    scope = parse_task_scope(issue_body)
    if scope is not None:
        if any(is_protected(path) for path in changed_paths) and scope.risk != "protected":
            raise ValueError("protected changed paths require risk: protected")
        return scope.risk
    return "protected" if any(is_protected(path) for path in changed_paths) else "standard"


def authorized_paths(issue_body: str) -> set[str]:
    return protected_authorized_paths(issue_body)

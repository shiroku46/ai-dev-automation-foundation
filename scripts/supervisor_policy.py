#!/usr/bin/env python3
"""Practical scope and risk helpers shared by trusted runtime and tests."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import re

PROTECTED_PREFIXES = (".github/", "bootstrap/")
PROTECTED_EXACT = {
    "SECURITY.md",
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
    "INSTALL_CHECKLIST.md",
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
    base = _pattern_base(pattern)
    if base in {".github", "bootstrap"}:
        return True
    return is_protected(base)


def _pattern_is_low_risk(pattern: str) -> bool:
    normalized = normalize_path(pattern)
    base = _pattern_base(normalized)
    if base in {"docs", "tests"}:
        return True
    if normalized in LOW_RISK_EXACT:
        return True
    if normalized.startswith(LOW_RISK_PREFIXES):
        return True
    # Markdown/text artifacts outside protected directories are non-runtime.
    return normalized.endswith(LOW_RISK_SUFFIXES) and not _pattern_is_protected(normalized)


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


def _block_value(lines: list[str], key: str) -> str:
    prefix = f"{key}:"
    for raw in lines:
        line = raw.strip()
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return ""


def parse_task_scope(issue_body: str) -> TaskScope | None:
    body = issue_body or ""
    if TASK_SCOPE_MARKER not in body:
        return None
    remainder = body.split(TASK_SCOPE_MARKER, 1)[1]
    if TASK_SCOPE_END not in remainder:
        raise ValueError("foundation-task-scope block is not terminated")
    block = remainder.split(TASK_SCOPE_END, 1)[0]
    lines = block.splitlines()
    risk = _block_value(lines, "risk").lower()
    operation = _block_value(lines, "operation")
    prohibited = _block_value(lines, "prohibited")
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
    """Return protected authorization patterns, preferring the unified scope."""
    scope = parse_task_scope(issue_body)
    if scope is not None:
        return set(scope.paths) if scope.risk == "protected" else set()
    marker = "<!-- foundation-protected-authorization"
    end = "-->"
    if marker not in (issue_body or ""):
        return set()
    block = issue_body.split(marker, 1)[1].split(end, 1)[0]
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
    for raw in (issue_body or "").splitlines():
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
    """Return unified task-scope paths, with bounded legacy compatibility."""
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


# Backward-compatible name used by earlier tests and consumers.
def authorized_paths(issue_body: str) -> set[str]:
    return protected_authorized_paths(issue_body)

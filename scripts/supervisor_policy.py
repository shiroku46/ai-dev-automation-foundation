#!/usr/bin/env python3
"""Small policy helpers shared by tests and trusted runtime."""
from __future__ import annotations
import re
from pathlib import PurePosixPath

PROTECTED_PREFIXES = (".github/", "bootstrap/")
PROTECTED_EXACT = {"SECURITY.md", "scripts/supervisor_runtime.py", "scripts/ai_recovery_supervisor.py"}

def is_protected(path: str) -> bool:
    normalized = str(PurePosixPath(path))
    return normalized in PROTECTED_EXACT or normalized.startswith(PROTECTED_PREFIXES)

def parse_issue_number(pr_body: str) -> int | None:
    match = re.search(r"(?im)\b(?:closes|fixes|resolves)\s+#(\d+)\b", pr_body or "")
    return int(match.group(1)) if match else None

def authorized_paths(issue_body: str) -> set[str]:
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
        if in_paths and line.startswith("- "):
            paths.add(line[2:].strip("` "))
        elif in_paths and line and not line.startswith("#"):
            in_paths = False
    return paths

def protected_scope_is_authorized(changed_paths, issue_body: str) -> bool:
    protected = {p for p in changed_paths if is_protected(p)}
    return protected <= authorized_paths(issue_body)

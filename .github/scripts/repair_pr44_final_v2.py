#!/usr/bin/env python3
"""Overlay the last live-snapshot ordering repair on the fixed PR #44 helper."""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

HELPER = Path(__file__).with_name("repair_pr44_final.py")
SPEC = importlib.util.spec_from_file_location("repair_pr44_final_base", HELPER)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load the fixed base repair helper")
BASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BASE)
BASE_REQUIRE_TARGET = BASE.require_target
SOURCE_LINK = re.compile(
    r"(?im)^\s*(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#36\.?\s*$"
)


def require_target(snapshot: dict) -> None:
    BASE_REQUIRE_TARGET(snapshot)
    if SOURCE_LINK.search(snapshot.get("body") or "") is None:
        raise RuntimeError("Target Pull Request lacks the exact source Issue linkage")
    issue = BASE.api(f"repos/{BASE.REPOSITORY}/issues/{BASE.SOURCE_ISSUE}")
    if issue.get("pull_request"):
        raise RuntimeError("Fixed source is not an Issue")
    owner = BASE.REPOSITORY.split("/", 1)[0]
    if (issue.get("user") or {}).get("login") != owner:
        raise RuntimeError("Fixed source Issue is not owner-authored")
    body = issue.get("body") or ""
    for path in (BASE.RUNTIME_PATH, BASE.TEST_PATH):
        if path not in body:
            raise RuntimeError(f"Fixed source Issue no longer authorizes {path}")


def repair_runtime(content: str) -> str:
    repaired = BASE.repair_runtime(content)
    old = '''        if not exact_codex_clean(pr_number, sha):
            continue
        merge_candidate = _live_pr(pr_number, sha)
        final_issue_number, _, _, final_scope_error = source_and_scope(merge_candidate)
        if final_issue_number != issue_number or final_scope_error:
            continue
        if (
            merge_candidate.get("mergeable") is not True
            or not trusted_candidate(merge_candidate)
            or parse_issue_number(merge_candidate.get("body") or "") != issue_number
        ):
            continue
        gh(
'''
    new = '''        if not exact_codex_clean(pr_number, sha):
            continue
        scope_candidate = _live_pr(pr_number, sha)
        final_issue_number, _, _, final_scope_error = source_and_scope(scope_candidate)
        if final_issue_number != issue_number or final_scope_error:
            continue
        merge_candidate = _live_pr(pr_number, sha)
        if (
            merge_candidate.get("mergeable") is not True
            or not trusted_candidate(merge_candidate)
            or parse_issue_number(merge_candidate.get("body") or "") != issue_number
        ):
            continue
        gh(
'''
    return BASE.replace_once(repaired, old, new, "post-scope live PR snapshot")


def repair_tests(content: str) -> str:
    repaired = BASE.repair_tests(content)
    repaired = BASE.replace_once(
        repaired,
        "            [clean, clean, clean, clean, clean, clean],\n",
        "            [clean, clean, clean, clean, clean, clean, clean],\n",
        "clean final-gate live snapshots",
    )
    old = '''        last_codex = supervise.rfind("if not exact_codex_clean(pr_number, sha):")
        last_scope = supervise.rfind("source_and_scope(merge_candidate)")
        merge = supervise.rfind('f"repos/{REPO}/pulls/{pr_number}/merge"')
        self.assertTrue(last_codex < last_scope < merge)
'''
    new = '''        last_codex = supervise.rfind("if not exact_codex_clean(pr_number, sha):")
        last_scope = supervise.rfind("source_and_scope(scope_candidate)")
        last_live = supervise.rfind("merge_candidate = _live_pr(pr_number, sha)")
        merge = supervise.rfind('f"repos/{REPO}/pulls/{pr_number}/merge"')
        self.assertTrue(last_codex < last_scope < last_live < merge)
'''
    return BASE.replace_once(repaired, old, new, "final merge ordering regression")


BASE.require_target = require_target
BASE.repair_runtime = repair_runtime
BASE.repair_tests = repair_tests

if __name__ == "__main__":
    raise SystemExit(BASE.main())

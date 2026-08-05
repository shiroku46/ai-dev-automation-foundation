"""Active GitHub coordinator scope, check, and security contracts."""
from __future__ import annotations

import unittest
from pathlib import Path

from scripts.github_coordinator_supervisor import is_protected_path, path_allowed
from scripts.supervisor_policy import (
    declared_paths,
    protected_scope_is_authorized,
    scope_is_authorized,
)

ROOT = Path(__file__).resolve().parents[1]


class SourceScopePolicyTest(unittest.TestCase):
    def test_all_changed_and_renamed_paths_must_match_issue_allowlist(self):
        body = """
## Allowed scope
- scripts/probe.py
- tests/**

<!-- foundation-protected-authorization
paths:
- scripts/github_coordinator_supervisor.py
operation: bounded
-->
"""
        self.assertEqual(declared_paths(body), {"scripts/probe.py", "tests/**"})
        self.assertTrue(scope_is_authorized(["scripts/probe.py", "tests/unit/test_probe.py"], body))
        self.assertFalse(scope_is_authorized(["scripts/github_coordinator_supervisor.py"], body))
        self.assertFalse(scope_is_authorized(["scripts/probe.py", "README.md"], body))

    def test_protected_paths_require_independent_declarations(self):
        protected_only = """
<!-- foundation-protected-authorization
paths:
- scripts/github_coordinator_supervisor.py
operation: bounded
-->
"""
        self.assertFalse(scope_is_authorized(["scripts/github_coordinator_supervisor.py"], protected_only))
        body = """
## Allowed paths
- scripts/github_coordinator_supervisor.py

<!-- foundation-protected-authorization
paths:
- scripts/github_coordinator_supervisor.py
operation: bounded
-->
"""
        self.assertTrue(scope_is_authorized(["scripts/github_coordinator_supervisor.py"], body))
        self.assertTrue(protected_scope_is_authorized(["scripts/github_coordinator_supervisor.py"], body))

    def test_invalid_or_unbounded_path_declarations_fail_closed(self):
        body = "## Allowed paths\n- ../outside.py\n- *.py\n- prose description here\n"
        self.assertEqual(declared_paths(body), {"*.py"})
        self.assertFalse(scope_is_authorized(["README.md"], body))
        self.assertFalse(scope_is_authorized(["probe.py"], body))


class ActiveCoordinatorContractTest(unittest.TestCase):
    def test_path_matching_is_exact_or_bounded_recursive(self):
        self.assertTrue(path_allowed("tests/unit/test_probe.py", ("tests/**",)))
        self.assertTrue(path_allowed("scripts/probe.py", ("scripts/probe.py",)))
        self.assertFalse(path_allowed("scripts/probe.py.bak", ("scripts/probe.py",)))
        self.assertFalse(path_allowed("../outside", ("tests/**",)))

    def test_protected_path_families_remain_enforced(self):
        for relative in (
            ".github/workflows/ci.yml",
            "bootstrap/generator.py",
            "scripts/github_coordinator_supervisor.py",
            "scripts/supervisor_policy.py",
        ):
            self.assertTrue(is_protected_path(relative), relative)
        self.assertFalse(is_protected_path("docs/product-note.md"))

    def test_coordinator_keeps_exact_head_review_and_merge_boundaries(self):
        runtime = (ROOT / "scripts/github_coordinator_supervisor.py").read_text(encoding="utf-8")
        for required in (
            "foundation-coordinator-review",
            "foundation-protected-authorization",
            "workflow differs from the default-branch definition",
            "exact-head check evidence changed during evaluation",
            "coordinator review evidence changed during evaluation",
            "expected-head merge was rejected",
            "unresolved_threads",
            "ai-no-merge",
        ):
            self.assertIn(required, runtime)
        for forbidden in ("secrets.", "anthropics/", "claude-code-action", "id-token: write"):
            self.assertNotIn(forbidden, runtime)

    def test_supervisor_workflow_is_provider_independent(self):
        workflow = (ROOT / ".github/workflows/supervisor.yml").read_text(encoding="utf-8")
        self.assertIn("python -m scripts.github_coordinator_supervisor", workflow)
        self.assertIn('workflows: ["CI", "Unit Tests"]', workflow)
        for forbidden in ("secrets.", "anthropics/", "codex", "id-token: write", "actions: write"):
            self.assertNotIn(forbidden, workflow)


if __name__ == "__main__":
    unittest.main()

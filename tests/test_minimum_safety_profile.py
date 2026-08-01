import unittest

from scripts.supervisor_policy import (
    parse_task_scope,
    protected_scope_is_authorized,
    risk_for_changes,
    scope_is_authorized,
)


class MinimumSafetyProfileTest(unittest.TestCase):
    def _scope(self, risk="standard", paths=None):
        paths = paths or ["src/**", "tests/**"]
        path_lines = "\n".join(f"- {path}" for path in paths)
        return f"""<!-- foundation-task-scope
risk: {risk}
paths:
{path_lines}
operation: implement the bounded task
prohibited: no Secrets, deployment, production, or unrelated changes
checks:
- CI
- product:test
-->"""

    def test_unified_scope_parses_risk_paths_and_checks(self):
        scope = parse_task_scope(self._scope())
        self.assertIsNotNone(scope)
        self.assertEqual(scope.risk, "standard")
        self.assertEqual(scope.paths, ("src/**", "tests/**"))
        self.assertEqual(scope.checks, ("CI", "product:test"))
        self.assertTrue(scope.operation)
        self.assertTrue(scope.prohibited)

    def test_unified_scope_authorizes_changed_and_renamed_paths(self):
        body = self._scope(paths=["src/**", "old/name.py", "new/name.py"])
        self.assertTrue(
            scope_is_authorized(
                ["src/app.py", "old/name.py", "new/name.py"],
                body,
            )
        )
        self.assertFalse(scope_is_authorized(["docs/outside.md"], body))

    def test_protected_paths_require_protected_risk(self):
        for protected_path in (".github/workflows/ci.yml", ".github/**", "bootstrap/**"):
            with self.subTest(protected_path=protected_path):
                with self.assertRaisesRegex(ValueError, "protected paths"):
                    parse_task_scope(self._scope("standard", [protected_path]))
        body = self._scope("protected", [".github/**", "tests/**"])
        self.assertEqual(
            risk_for_changes([".github/workflows/ci.yml"], body),
            "protected",
        )
        self.assertTrue(
            protected_scope_is_authorized([".github/workflows/ci.yml"], body)
        )

    def test_low_risk_cannot_hide_protected_change(self):
        body = self._scope("low", ["docs/**"])
        self.assertEqual(risk_for_changes(["docs/readme.md"], body), "low")
        self.assertFalse(scope_is_authorized(["scripts/runtime.py"], body))

    def test_missing_required_fields_fail_closed(self):
        for block in (
            "<!-- foundation-task-scope\nrisk: low\npaths:\n- docs/**\nprohibited: none\n-->",
            "<!-- foundation-task-scope\nrisk: low\npaths:\n- docs/**\noperation: docs\n-->",
            "<!-- foundation-task-scope\nrisk: unknown\npaths:\n- docs/**\noperation: docs\nprohibited: none\n-->",
        ):
            with self.subTest(block=block):
                with self.assertRaises(ValueError):
                    parse_task_scope(block)

    def test_legacy_scope_remains_temporarily_supported(self):
        legacy = """## Allowed paths
- docs/example.md

<!-- foundation-protected-authorization
paths:
- docs/example.md
-->"""
        self.assertTrue(scope_is_authorized(["docs/example.md"], legacy))
        self.assertEqual(risk_for_changes(["docs/example.md"], legacy), "standard")


if __name__ == "__main__":
    unittest.main()

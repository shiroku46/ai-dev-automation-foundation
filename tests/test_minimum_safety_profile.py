import unittest

from scripts.supervisor_policy import (
    is_protected,
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

    def test_fenced_scope_examples_are_not_authorization(self):
        example = self._scope("low", ["docs/example.md"])
        actual = self._scope("protected", ["scripts/supervisor_policy.py"])
        body = f"""The following block is only an example:

```text
{example}
```

The actual authorization follows:

{actual}
"""
        scope = parse_task_scope(body)
        self.assertIsNotNone(scope)
        self.assertEqual(scope.risk, "protected")
        self.assertEqual(scope.paths, ("scripts/supervisor_policy.py",))
        self.assertTrue(
            scope_is_authorized(["scripts/supervisor_policy.py"], body)
        )
        self.assertFalse(scope_is_authorized(["docs/example.md"], body))

    def test_fenced_example_without_actual_scope_falls_back_to_legacy(self):
        example = self._scope("protected", [".github/**"])
        body = f"""```markdown
{example}
## Allowed paths
- forbidden/example.py
```

## Allowed paths
- docs/actual.md
"""
        self.assertIsNone(parse_task_scope(body))
        self.assertTrue(scope_is_authorized(["docs/actual.md"], body))
        self.assertFalse(scope_is_authorized(["forbidden/example.py"], body))

    def test_fenced_legacy_protected_example_is_ignored(self):
        body = """```text
<!-- foundation-protected-authorization
paths:
- .github/workflows/fake.yml
-->
```

## Allowed paths
- .github/workflows/real.yml

<!-- foundation-protected-authorization
paths:
- .github/workflows/real.yml
-->
"""
        self.assertTrue(
            protected_scope_is_authorized(
                [".github/workflows/real.yml"], body
            )
        )
        self.assertFalse(
            protected_scope_is_authorized(
                [".github/workflows/fake.yml"], body
            )
        )

    def test_exactly_one_task_scope_block_is_required(self):
        valid = self._scope()
        with self.assertRaisesRegex(ValueError, "exactly one"):
            parse_task_scope(f"{valid}\n{valid}")
        with self.assertRaisesRegex(ValueError, "exactly one"):
            parse_task_scope(f"{valid}\n<!-- foundation-task-scope\nrisk: low")

    def test_scalar_scope_fields_and_sections_are_unique(self):
        cases = {
            "risk": self._scope().replace(
                "risk: standard", "risk: standard\nrisk: protected"
            ),
            "operation": self._scope().replace(
                "operation: implement the bounded task",
                "operation: implement the bounded task\noperation: another operation",
            ),
            "prohibited": self._scope().replace(
                "prohibited: no Secrets, deployment, production, or unrelated changes",
                "prohibited: no Secrets\nprohibited: no deployment",
            ),
            "paths": self._scope().replace(
                "paths:\n", "paths:\n- src/a.py\npaths:\n", 1
            ),
            "checks": self._scope().replace(
                "checks:\n", "checks:\n- lint\nchecks:\n", 1
            ),
        }
        for field, body in cases.items():
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "exactly one"):
                    parse_task_scope(body)

    def test_protected_paths_and_policy_documents_require_protected_risk(self):
        protected_exact = (
            ".github/workflows/ci.yml",
            "AGENTS.md",
            "CLAUDE.md",
            "INSTALL_CHECKLIST.md",
            "docs/MINIMUM_SAFETY_PROFILE.md",
            "docs/OPERATING_RULES.md",
            "docs/PUBLIC_SECURITY_MODEL.md",
            "scripts/public_export_guard.py",
            "scripts/validate_repository.py",
        )
        for protected_path in protected_exact:
            with self.subTest(protected_path=protected_path):
                self.assertTrue(is_protected(protected_path))
                with self.assertRaisesRegex(ValueError, "protected paths"):
                    parse_task_scope(self._scope("standard", [protected_path]))

        protected_patterns = (
            ".github/**",
            "bootstrap/**",
            "docs/**",
            "scripts/**",
        )
        for protected_pattern in protected_patterns:
            with self.subTest(protected_pattern=protected_pattern):
                with self.assertRaisesRegex(ValueError, "protected paths"):
                    parse_task_scope(self._scope("standard", [protected_pattern]))

        body = self._scope("protected", [".github/**", "tests/**"])
        self.assertEqual(
            risk_for_changes([".github/workflows/ci.yml"], body),
            "protected",
        )
        self.assertTrue(
            protected_scope_is_authorized([".github/workflows/ci.yml"], body)
        )

    def test_installation_and_validator_files_cannot_be_low_risk(self):
        for path in (
            "INSTALL_CHECKLIST.md",
            "scripts/public_export_guard.py",
            "scripts/validate_repository.py",
        ):
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, "protected paths"):
                    parse_task_scope(self._scope("low", [path]))

    def test_low_risk_is_restricted_to_non_runtime_paths(self):
        body = self._scope(
            "low", ["docs/user-guide.md", "tests/**", "README.md"]
        )
        self.assertEqual(risk_for_changes(["docs/user-guide.md"], body), "low")
        self.assertTrue(scope_is_authorized(["tests/test_docs.py"], body))
        for executable in (
            "src/**",
            "app/main.py",
            "scripts/tool.py",
            "docs/**",
            "docs/OPERATING_RULES.md",
            "INSTALL_CHECKLIST.md",
        ):
            with self.subTest(executable=executable):
                with self.assertRaisesRegex(
                    ValueError, "low-risk paths|protected paths"
                ):
                    parse_task_scope(self._scope("low", [executable]))

    def test_missing_required_fields_fail_closed(self):
        for block in (
            "<!-- foundation-task-scope\nrisk: low\npaths:\n- docs/user-guide.md\nprohibited: none\nchecks:\n- CI\n-->",
            "<!-- foundation-task-scope\nrisk: low\npaths:\n- docs/user-guide.md\noperation: docs\nchecks:\n- CI\n-->",
            "<!-- foundation-task-scope\nrisk: low\npaths:\n- docs/user-guide.md\noperation: docs\nprohibited: none\n-->",
            "<!-- foundation-task-scope\nrisk: unknown\npaths:\n- docs/user-guide.md\noperation: docs\nprohibited: none\nchecks:\n- CI\n-->",
        ):
            with self.subTest(block=block):
                with self.assertRaises(ValueError):
                    parse_task_scope(block)

    def test_duplicate_checks_fail_closed(self):
        duplicate = self._scope().replace("- product:test", "- CI")
        with self.assertRaisesRegex(ValueError, "checks must be unique"):
            parse_task_scope(duplicate)

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

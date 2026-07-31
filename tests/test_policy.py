import unittest
from scripts.supervisor_policy import *

class PolicyTest(unittest.TestCase):
    def test_issue_and_authorization(self):
        self.assertEqual(parse_issue_number("Closes #42."), 42)
        body = """<!-- foundation-protected-authorization
category: workflow
paths:
- .github/workflows/x.yml
operation: update
-->"""
        self.assertEqual(authorized_paths(body), {".github/workflows/x.yml"})
        self.assertTrue(protected_scope_is_authorized([".github/workflows/x.yml"], body))
        self.assertFalse(protected_scope_is_authorized([".github/workflows/y.yml"], body))

    def test_protected_classification(self):
        self.assertTrue(is_protected(".github/workflows/x.yml"))
        self.assertTrue(is_protected("bootstrap/generator.py"))
        self.assertFalse(is_protected("docs/example.md"))

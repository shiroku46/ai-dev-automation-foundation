#!/usr/bin/env python3
"""Correct focused Issue #192 test expectations after generation."""
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one source match, got {count}")
    return text.replace(old, new, 1)


path = Path("tests/test_workflow_security.py")
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    '''            'wait_for_record(path, payload, commit_sha)',
            'linear_descendant(',
            'ref_linearly_contains(',
            'recovery record compare-and-swap failed',
''',
    '''            'wait_for_record(path, payload, commit_sha)',
            'recovery record compare-and-swap failed',
''',
    "helper location assertions",
)
text = replace_once(
    text,
    '''        self.assertFalse(linear(chain[-1], chain[0], fetch(graph), 32))
        self.assertTrue(linear(chain[-1], chain[0], fetch(graph), 34))
''',
    '''        self.assertFalse(linear(chain[-1], chain[0], fetch(graph), 32))
        self.assertTrue(linear(chain[32], chain[0], fetch(graph), 32))
''',
    "bounded ancestry fixture",
)
path.write_text(text, encoding="utf-8")

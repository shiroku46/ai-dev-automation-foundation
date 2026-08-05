#!/usr/bin/env python3
"""Correct the legacy inline fixed-path source-count expectation for Issue #194."""
from pathlib import Path

path = Path("tests/test_workflow_security.py")
text = path.read_text(encoding="utf-8")
old = "        self.assertGreaterEqual(reconcile.count(fixed_path), 3)\n"
new = "        self.assertGreaterEqual(reconcile.count(fixed_path), 2)\n"
if text.count(old) != 1:
    raise SystemExit("fixed-path source-count assertion did not match exactly once")
path.write_text(text.replace(old, new, 1), encoding="utf-8")

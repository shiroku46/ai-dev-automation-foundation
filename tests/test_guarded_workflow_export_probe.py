"""Temporary public-runner export probe for guarded workflow bytes."""
from __future__ import annotations

import base64
import unittest
from pathlib import Path

from scripts.private_actions_guard import guard_private_actions_workflow

ROOT = Path(__file__).resolve().parents[1]


class GuardedWorkflowExportProbe(unittest.TestCase):
    def test_export_ci_reconcile(self):
        content = guard_private_actions_workflow(
            (ROOT / ".github/workflows/ci-reconcile.yml").read_bytes()
        )
        print("GUARDED_CI_RECONCILE_BASE64=" + base64.b64encode(content).decode("ascii"))
        self.assertGreater(len(content), 0)


if __name__ == "__main__":
    unittest.main()

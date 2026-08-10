"""Temporary public-runner export probe for guarded target bytes."""
from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from bootstrap.generator import install_checklist
from scripts.private_actions_guard import FOUNDATION_WORKFLOW_PATHS, guard_private_actions_workflow

ROOT = Path(__file__).resolve().parents[1]
NON_WORKFLOW_PATHS = (
    "docs/FREE_ONLY_OPERATING_PROFILE.md",
    "scripts/private_actions_guard.py",
    "scripts/validate_repository.py",
)


class GuardedWorkflowExportProbe(unittest.TestCase):
    def test_print_target_hash_manifest(self):
        manifest = {
            relative: hashlib.sha256(
                guard_private_actions_workflow((ROOT / relative).read_bytes())
            ).hexdigest()
            for relative in FOUNDATION_WORKFLOW_PATHS
        }
        for relative in NON_WORKFLOW_PATHS:
            manifest[relative] = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        manifest["INSTALL_CHECKLIST.md"] = hashlib.sha256(
            install_checklist("shiroku46", "existing-product").encode("utf-8")
        ).hexdigest()
        print("PRIVATE_TARGET_HASH_MANIFEST=" + json.dumps(manifest, sort_keys=True))
        self.assertEqual(len(manifest), len(FOUNDATION_WORKFLOW_PATHS) + 4)


if __name__ == "__main__":
    unittest.main()

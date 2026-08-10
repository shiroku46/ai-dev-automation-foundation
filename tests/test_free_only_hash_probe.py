"""Temporary public-runner probe for accepted free-only Bootstrap hashes."""
from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from bootstrap.generator import install_checklist

ROOT = Path(__file__).resolve().parents[1]
PATHS = (
    "docs/FREE_ONLY_OPERATING_PROFILE.md",
    "scripts/external_validation.py",
    "scripts/foundation_product_checks.py",
    "scripts/free_only_coordinator.py",
    "scripts/validate_repository.py",
)


class FreeOnlyHashProbe(unittest.TestCase):
    def test_print_manifest(self):
        manifest = {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for path in PATHS
        }
        manifest["INSTALL_CHECKLIST.md"] = hashlib.sha256(
            install_checklist("shiroku46", "existing-product").encode("utf-8")
        ).hexdigest()
        print("FREE_ONLY_HASH_MANIFEST=" + json.dumps(manifest, sort_keys=True))
        self.assertEqual(len(manifest), 6)


if __name__ == "__main__":
    unittest.main()

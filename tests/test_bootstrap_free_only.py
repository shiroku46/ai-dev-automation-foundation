"""Focused Bootstrap regressions for the free-only execution profile."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from bootstrap.generator import MANAGED_FILES, render

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA = "a" * 40
INSTALLED_AT = "2026-08-11T00:00:00Z"
FREE_ONLY_MANAGED = {
    "docs/FREE_ONLY_OPERATING_PROFILE.md",
    "scripts/external_validation.py",
    "scripts/free_only_coordinator.py",
}


def render_new(target: Path) -> None:
    render(
        target,
        "owner",
        mode="new-repository",
        source_sha=SOURCE_SHA,
        installed_at=INSTALLED_AT,
    )


def validate(target: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/validate_repository.py"],
        cwd=target,
        capture_output=True,
        text=True,
        check=False,
    )


class FreeOnlyBootstrapTest(unittest.TestCase):
    def test_free_only_runtime_is_managed_and_byte_identical(self):
        self.assertTrue(FREE_ONLY_MANAGED.issubset(MANAGED_FILES))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render_new(target)
            lock = json.loads((target / "FOUNDATION.lock.json").read_text(encoding="utf-8"))
            locked = {item["path"] for item in lock["managed_files"]}
            self.assertTrue(FREE_ONLY_MANAGED.issubset(locked))
            for relative in FREE_ONLY_MANAGED:
                self.assertEqual((target / relative).read_bytes(), (ROOT / relative).read_bytes())
            result = validate(target)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_checklist_uses_free_only_cost_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render_new(target)
            checklist = (target / "INSTALL_CHECKLIST.md").read_text(encoding="utf-8")
            for marker in (
                "Mandatory SCM and validation setup",
                "execution_profile: free-only",
                "private GitHub-hosted Actions are not a mandatory gate",
                "Cloudflare Workers Builds is the first supported external validator",
                "OpenAI API usage",
                "new paid plan, overage, payment method, or API-billed AI service",
                "candidate cannot self-authorize",
            ):
                self.assertIn(marker, checklist)
            self.assertNotIn("Require exact-head `CI` and `Unit Tests`.", checklist)

    def test_existing_product_preserves_valid_free_only_target_config(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render_new(target)
            config = target / ".github/foundation-product-checks.json"
            free_only = {
                "schema_version": 2,
                "execution_profile": "free-only",
                "checks": [
                    {
                        "kind": "external",
                        "name": "Cloudflare validation",
                        "provider": "cloudflare-workers-builds",
                        "check_name": "Workers Builds: product",
                        "app_slug": "cloudflare-workers-and-pages",
                        "app_id": 85455,
                    }
                ],
            }
            expected = json.dumps(free_only, indent=2, sort_keys=True) + "\n"
            config.write_text(expected, encoding="utf-8")
            plan = render(
                target,
                "owner",
                mode="existing-product",
                source_sha="b" * 40,
                installed_at="2026-08-11T00:10:00Z",
            )
            self.assertIn(".github/foundation-product-checks.json", plan.preserved)
            self.assertEqual(config.read_text(encoding="utf-8"), expected)
            result = validate(target)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

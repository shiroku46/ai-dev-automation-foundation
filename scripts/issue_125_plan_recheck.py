#!/usr/bin/env python3
"""Add a complete target-state recheck immediately before Bootstrap writes."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


generator_path = ROOT / "bootstrap/generator.py"
generator = generator_path.read_text(encoding="utf-8")
generator = replace_once(
    generator,
    '''def _lock_payload(
''',
    '''def _verify_plan_state(target: Path, plan: RenderPlan) -> None:
    """Fail before mutation when any planned destination changed after planning."""
    for entry in plan.entries:
        destination = _assert_safe_destination(target, entry.path)
        if entry.action == "add":
            if destination.exists():
                raise ValueError(f"target changed after Bootstrap plan: {entry.path}")
            continue
        if entry.action == "collision":
            raise ValueError(f"unsafe collision remained in Bootstrap plan: {entry.path}")
        if destination.is_symlink() or not destination.is_file():
            raise ValueError(f"target changed after Bootstrap plan: {entry.path}")
        current_digest = _sha256_file(destination)
        if current_digest != entry.target_sha256:
            raise ValueError(f"target changed after Bootstrap plan: {entry.path}")


def _lock_payload(
''',
    "plan-state verifier",
)
generator = replace_once(
    generator,
    '''    resolved_time = installed_at or _installed_at()
    if not isinstance(resolved_time, str) or not resolved_time:
        raise ValueError("installed_at must be a nonempty string")

    target.mkdir(parents=True, exist_ok=True)
''',
    '''    resolved_time = installed_at or _installed_at()
    if not isinstance(resolved_time, str) or not resolved_time:
        raise ValueError("installed_at must be a nonempty string")
    _verify_plan_state(target, plan)

    target.mkdir(parents=True, exist_ok=True)
''',
    "pre-write plan recheck",
)
generator_path.write_text(generator, encoding="utf-8")

test_path = ROOT / "tests/test_bootstrap.py"
tests = test_path.read_text(encoding="utf-8")
tests = replace_once(
    tests,
    '''import tempfile
import unittest
from pathlib import Path
''',
    '''import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
''',
    "mock import",
)
anchor = '''    def test_invalid_source_identity_aborts_before_any_write(self):
'''
addition = '''    def test_target_change_after_plan_aborts_before_any_foundation_write(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            product = target / "README.md"
            product.write_text("product README\\n", encoding="utf-8")
            real_plan = plan_render(target, "owner", mode="existing-product")

            def changed_plan(*_args, **_kwargs):
                workflow = target / ".github/workflows/ci.yml"
                workflow.parent.mkdir(parents=True, exist_ok=True)
                workflow.write_text("concurrent product workflow\\n", encoding="utf-8")
                return real_plan

            with patch("bootstrap.generator.plan_render", side_effect=changed_plan):
                with self.assertRaisesRegex(ValueError, "target changed after Bootstrap plan"):
                    render(
                        target,
                        "owner",
                        mode="existing-product",
                        source_sha=SOURCE_SHA,
                        installed_at=INSTALLED_AT,
                    )
            self.assertEqual(product.read_text(encoding="utf-8"), "product README\\n")
            self.assertEqual(
                (target / ".github/workflows/ci.yml").read_text(encoding="utf-8"),
                "concurrent product workflow\\n",
            )
            self.assertFalse((target / LOCK_FILE).exists())
            self.assertFalse((target / "scripts").exists())

'''
if tests.count(anchor) != 1:
    raise SystemExit("plan recheck test insertion point changed")
tests = tests.replace(anchor, addition + anchor, 1)
test_path.write_text(tests, encoding="utf-8")

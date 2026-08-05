"""Bootstrap planning, byte parity and generated-target safety tests."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bootstrap.generator import (
    ALLOWLIST,
    GENERATED_TARGET_MARKER,
    GENERATOR_VERSION,
    LOCK_FILE,
    MANAGED_FILES,
    PRESERVE_IF_PRESENT,
    plan_render,
    render,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA = "a" * 40
INSTALLED_AT = "2026-08-05T00:00:00Z"


def validate(target: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/validate_repository.py"],
        cwd=target,
        capture_output=True,
        text=True,
        check=False,
    )


def render_new(target: Path):
    return render(
        target,
        "owner",
        mode="new-repository",
        source_sha=SOURCE_SHA,
        installed_at=INSTALLED_AT,
    )


class BootstrapTest(unittest.TestCase):
    def test_allowlist_alias_and_required_runtime_dependencies(self):
        self.assertEqual(ALLOWLIST, MANAGED_FILES)
        self.assertNotIn("bootstrap/generator.py", MANAGED_FILES)
        self.assertFalse(any(path.startswith("tests/") for path in MANAGED_FILES))
        for required in (
            "scripts/foundation_drift.py",
            "scripts/queue_issue_hydration.py",
            "scripts/queue_retry_identity.py",
            "scripts/queue_event_guard.py",
            "scripts/github_api_governor.py",
            "scripts/supervisor_policy.py",
            ".github/workflows/claude-queue-comment-bridge.yml",
        ):
            self.assertIn(required, MANAGED_FILES)
        for retired in (
            "scripts/ai_recovery_supervisor.py",
            "scripts/supervisor_final_guard.py",
            "scripts/supervisor_runtime.py",
            "scripts/supervisor_queue_recovery.py",
            "scripts/supervisor_queue_recovery_v2.py",
            "scripts/supervisor_queue_recovery_v3.py",
        ):
            self.assertNotIn(retired, MANAGED_FILES)

    def test_plan_only_does_not_create_target(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target"
            plan = plan_render(target, "owner", mode="new-repository")
            self.assertTrue(plan.is_safe)
            self.assertFalse(target.exists())
            self.assertIn(LOCK_FILE, plan.writes)

    def test_new_repository_copies_managed_files_and_writes_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            plan = render_new(target)
            self.assertTrue(plan.is_safe)
            for relative in MANAGED_FILES:
                self.assertEqual(
                    (target / relative).read_bytes(),
                    (ROOT / relative).read_bytes(),
                    relative,
                )
            lock = json.loads((target / LOCK_FILE).read_text(encoding="utf-8"))
            self.assertEqual(lock["schema_version"], 1)
            self.assertEqual(lock["generator_version"], GENERATOR_VERSION)
            self.assertEqual(lock["source_sha"], SOURCE_SHA)
            self.assertEqual(lock["installation_mode"], "new-repository")
            self.assertEqual(lock["installed_at"], INSTALLED_AT)
            paths = [item["path"] for item in lock["managed_files"]]
            self.assertEqual(paths, sorted(paths))
            self.assertEqual(set(paths), set(MANAGED_FILES) | {"INSTALL_CHECKLIST.md"})
            for item in lock["managed_files"]:
                digest = hashlib.sha256((target / item["path"]).read_bytes()).hexdigest()
                self.assertEqual(item["sha256"], digest)
            self.assertFalse((target / "bootstrap").exists())
            self.assertFalse((target / "tests").exists())

    def test_existing_product_preserves_top_level_product_files(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            originals = {}
            for relative in sorted(PRESERVE_IF_PRESENT):
                content = f"product-owned {relative}\n".encode()
                (target / relative).write_bytes(content)
                originals[relative] = content
            (target / "package.json").write_text('{"private":true}\n', encoding="utf-8")
            plan = render(
                target,
                "owner",
                mode="existing-product",
                source_sha=SOURCE_SHA,
                installed_at=INSTALLED_AT,
            )
            self.assertEqual(set(plan.preserved), set(PRESERVE_IF_PRESENT))
            for relative, content in originals.items():
                self.assertEqual((target / relative).read_bytes(), content)
            self.assertEqual((target / "package.json").read_text(), '{"private":true}\n')
            lock = json.loads((target / LOCK_FILE).read_text(encoding="utf-8"))
            locked = {item["path"] for item in lock["managed_files"]}
            self.assertTrue(set(PRESERVE_IF_PRESENT).isdisjoint(locked))
            self.assertIn("scripts/validate_repository.py", locked)

    def test_collision_aborts_before_any_foundation_write(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            workflow = target / ".github/workflows/ci.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text("product workflow\n", encoding="utf-8")
            before = {path.relative_to(target): path.read_bytes() for path in target.rglob("*") if path.is_file()}
            with self.assertRaisesRegex(ValueError, "Bootstrap collisions"):
                render(
                    target,
                    "owner",
                    mode="existing-product",
                    source_sha=SOURCE_SHA,
                    installed_at=INSTALLED_AT,
                )
            after = {path.relative_to(target): path.read_bytes() for path in target.rglob("*") if path.is_file()}
            self.assertEqual(after, before)
            self.assertFalse((target / LOCK_FILE).exists())
            self.assertFalse((target / "scripts").exists())

    def test_exact_authorized_overwrite_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            workflow = target / ".github/workflows/ci.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text("product workflow\n", encoding="utf-8")
            plan = render(
                target,
                "owner",
                mode="existing-product",
                source_sha=SOURCE_SHA,
                installed_at=INSTALLED_AT,
                authorize_overwrite=[".github/workflows/ci.yml"],
            )
            self.assertIn(".github/workflows/ci.yml", plan.writes)
            self.assertEqual(workflow.read_bytes(), (ROOT / ".github/workflows/ci.yml").read_bytes())
            with self.assertRaises(ValueError):
                plan_render(target, "owner", mode="existing-product", authorize_overwrite=["../bad"])

    def test_locked_unchanged_file_upgrades_without_blanket_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render_new(target)
            old = target / "docs/PROJECT_STARTUP.md"
            lock = json.loads((target / LOCK_FILE).read_text(encoding="utf-8"))
            old_digest = next(item["sha256"] for item in lock["managed_files"] if item["path"] == "docs/PROJECT_STARTUP.md")
            self.assertEqual(hashlib.sha256(old.read_bytes()).hexdigest(), old_digest)
            plan = render(
                target,
                "owner",
                mode="existing-product",
                source_sha="b" * 40,
                installed_at="2026-08-06T00:00:00Z",
            )
            self.assertTrue(plan.is_safe)
            self.assertEqual(json.loads((target / LOCK_FILE).read_text())["source_sha"], "b" * 40)

    def test_modified_locked_file_is_collision(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render_new(target)
            custom = target / "docs/PROJECT_STARTUP.md"
            custom.write_text("customized\n", encoding="utf-8")
            before = custom.read_bytes()
            with self.assertRaisesRegex(ValueError, "PROJECT_STARTUP"):
                render(
                    target,
                    "owner",
                    mode="existing-product",
                    source_sha="b" * 40,
                    installed_at="2026-08-06T00:00:00Z",
                )
            self.assertEqual(custom.read_bytes(), before)

    def test_install_checklist_describes_github_only_non_destructive_path(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render_new(target)
            checklist = (target / "INSTALL_CHECKLIST.md").read_text(encoding="utf-8")
            for marker in (
                GENERATED_TARGET_MARKER,
                "## Phase 0",
                "Codex and Claude setup is optional",
                "FOUNDATION.lock.json",
                "## Non-destructive publication",
                "connected GitHub App/API route",
                "Draft Pull Request",
                "Do not create a temporary installer workflow",
                "BOOTSTRAP_WORKFLOW_TOKEN",
                "Do not force-update",
                "human_action_required: false",
            ):
                self.assertIn(marker, checklist)

    def test_generated_target_validator_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render_new(target)
            result = validate(target)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_generated_target_workflows_are_exact_source_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render_new(target)
            for relative in (
                ".github/workflows/ci.yml",
                ".github/workflows/unit-tests.yml",
                ".github/workflows/claude-queue.yml",
                ".github/workflows/ci-reconcile.yml",
                ".github/workflows/supervisor.yml",
            ):
                self.assertEqual((target / relative).read_bytes(), (ROOT / relative).read_bytes())

    def test_tampering_or_missing_managed_file_fails_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render_new(target)
            (target / ".github/workflows/supervisor.yml").write_text(
                "name: broken\non:\njobs:\n", encoding="utf-8"
            )
            result = validate(target)
            self.assertNotEqual(result.returncode, 0)

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render_new(target)
            (target / "scripts/queue_issue_hydration.py").unlink()
            result = validate(target)
            self.assertNotEqual(result.returncode, 0)

    def test_target_change_after_plan_aborts_before_any_foundation_write(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            product = target / "README.md"
            product.write_text("product README\n", encoding="utf-8")
            real_plan = plan_render(target, "owner", mode="existing-product")

            def changed_plan(*_args, **_kwargs):
                workflow = target / ".github/workflows/ci.yml"
                workflow.parent.mkdir(parents=True, exist_ok=True)
                workflow.write_text("concurrent product workflow\n", encoding="utf-8")
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
            self.assertEqual(product.read_text(encoding="utf-8"), "product README\n")
            self.assertEqual(
                (target / ".github/workflows/ci.yml").read_text(encoding="utf-8"),
                "concurrent product workflow\n",
            )
            self.assertFalse((target / LOCK_FILE).exists())
            self.assertFalse((target / "scripts").exists())

    def test_invalid_source_identity_aborts_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target"
            with self.assertRaisesRegex(ValueError, "source_sha"):
                render(
                    target,
                    "owner",
                    mode="new-repository",
                    source_sha="bad",
                    installed_at=INSTALLED_AT,
                )
            self.assertFalse(target.exists())

    def test_invalid_existing_lock_identity_aborts_before_write(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / LOCK_FILE).write_text(json.dumps({
                "schema_version": 1,
                "generator_version": GENERATOR_VERSION,
                "source_repository": "other/repository",
                "source_sha": SOURCE_SHA,
                "installation_mode": "existing-product",
                "installed_at": INSTALLED_AT,
                "managed_files": [],
            }), encoding="utf-8")
            before = (target / LOCK_FILE).read_bytes()
            with self.assertRaisesRegex(ValueError, "source repository"):
                render(
                    target,
                    "owner",
                    mode="existing-product",
                    source_sha=SOURCE_SHA,
                    installed_at=INSTALLED_AT,
                )
            self.assertEqual((target / LOCK_FILE).read_bytes(), before)
            self.assertFalse((target / "scripts").exists())

    def test_target_ancestor_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / "real"
            real.mkdir()
            link = root / "link"
            try:
                link.symlink_to(real, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("directory symlinks are unavailable")
            target = link / "target"
            with self.assertRaisesRegex(ValueError, "contains a symlink"):
                plan_render(target, "owner", mode="new-repository")
            self.assertFalse((real / "target").exists())

    def test_renderer_rejects_nonempty_new_mode_and_source_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "product.txt").write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not empty"):
                render_new(target)
        with self.assertRaises(ValueError):
            render(ROOT, "owner", mode="existing-product", source_sha=SOURCE_SHA)


if __name__ == "__main__":
    unittest.main()

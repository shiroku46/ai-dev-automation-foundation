"""Foundation lock drift and upgrade planner tests."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from bootstrap.generator import LOCK_FILE, render
from scripts.foundation_drift import LockError, inspect, load_lock

SOURCE_SHA = "a" * 40
INSTALLED_AT = "2026-08-05T00:00:00Z"


def render_target(target: Path) -> None:
    render(
        target,
        "owner",
        mode="new-repository",
        source_sha=SOURCE_SHA,
        installed_at=INSTALLED_AT,
    )


def write_lock(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class FoundationDriftTest(unittest.TestCase):
    def test_clean_target_reports_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render_target(target)
            report = inspect(target)
            self.assertEqual(report["status"], "clean")
            self.assertFalse(report["human_action_required"])
            self.assertTrue(report["entries"])
            self.assertEqual({item["state"] for item in report["entries"]}, {"unchanged"})

    def test_modified_and_missing_managed_files_are_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render_target(target)
            (target / "docs/PROJECT_STARTUP.md").write_text("custom\n", encoding="utf-8")
            (target / "scripts/queue_retry_identity.py").unlink()
            report = inspect(target)
            states = {item["path"]: item["state"] for item in report["entries"]}
            self.assertEqual(report["status"], "drift")
            self.assertEqual(states["docs/PROJECT_STARTUP.md"], "modified")
            self.assertEqual(states["scripts/queue_retry_identity.py"], "missing")

    def test_expected_lock_identifies_stale_and_new_files(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render_target(target)
            current = load_lock(target / LOCK_FILE)
            expected = json.loads(json.dumps(current))
            expected["source_sha"] = "b" * 40
            startup = next(item for item in expected["managed_files"] if item["path"] == "docs/PROJECT_STARTUP.md")
            startup["sha256"] = "c" * 64
            expected["managed_files"].append({"path": "scripts/new_foundation_file.py", "sha256": "d" * 64})
            expected["managed_files"].sort(key=lambda item: item["path"])
            report = inspect(target, expected)
            states = {item["path"]: item["state"] for item in report["entries"]}
            self.assertEqual(report["status"], "drift")
            self.assertEqual(states["docs/PROJECT_STARTUP.md"], "stale")
            self.assertEqual(states["scripts/new_foundation_file.py"], "new")
            self.assertEqual(report["expected_source_sha"], "b" * 40)

    def test_customized_stale_file_and_new_path_collision_block_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render_target(target)
            current = load_lock(target / LOCK_FILE)
            expected = json.loads(json.dumps(current))
            expected["source_sha"] = "b" * 40
            startup = next(item for item in expected["managed_files"] if item["path"] == "docs/PROJECT_STARTUP.md")
            startup["sha256"] = "c" * 64
            (target / "docs/PROJECT_STARTUP.md").write_text("custom\n", encoding="utf-8")
            new_path = target / "scripts/new_foundation_file.py"
            new_path.write_text("product-owned\n", encoding="utf-8")
            expected["managed_files"].append({
                "path": "scripts/new_foundation_file.py",
                "sha256": hashlib.sha256(b"foundation\n").hexdigest(),
            })
            expected["managed_files"].sort(key=lambda item: item["path"])
            report = inspect(target, expected)
            states = {item["path"]: item["state"] for item in report["entries"]}
            self.assertEqual(report["status"], "blocked")
            self.assertEqual(states["docs/PROJECT_STARTUP.md"], "collision")
            self.assertEqual(states["scripts/new_foundation_file.py"], "collision")
            self.assertFalse(report["human_action_required"])

    def test_malformed_unsorted_or_unsafe_lock_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = root / LOCK_FILE
            lock.write_text("{}", encoding="utf-8")
            with self.assertRaises(LockError):
                load_lock(lock)

            value = {
                "schema_version": 1,
                "source_repository": "owner/repo",
                "source_sha": "a" * 40,
                "managed_files": [
                    {"path": "z", "sha256": "a" * 64},
                    {"path": "a", "sha256": "b" * 64},
                ],
            }
            write_lock(lock, value)
            with self.assertRaisesRegex(LockError, "not sorted"):
                load_lock(lock)

            value["managed_files"] = [{"path": "../bad", "sha256": "a" * 64}]
            write_lock(lock, value)
            with self.assertRaises(LockError):
                load_lock(lock)

    def test_cli_exit_codes_are_machine_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render_target(target)
            clean = subprocess.run(
                [sys.executable, "scripts/foundation_drift.py", "--root", "."],
                cwd=target,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)
            self.assertEqual(json.loads(clean.stdout)["status"], "clean")
            (target / "docs/PROJECT_STARTUP.md").write_text("custom\n", encoding="utf-8")
            drift = subprocess.run(
                [sys.executable, "scripts/foundation_drift.py", "--root", "."],
                cwd=target,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(drift.returncode, 1)
            self.assertEqual(json.loads(drift.stdout)["status"], "drift")


if __name__ == "__main__":
    unittest.main()

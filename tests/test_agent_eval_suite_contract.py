"""Deterministic evaluation suite catalog and bundle tests."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import agent_eval_suite_contract as suite

ROOT = Path(__file__).resolve().parents[1]


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def task_payload(fixture: suite.DirectoryBundle, grader: suite.DirectoryBundle) -> dict:
    return {
        "schema_version": 1,
        "task_id": "foundation.task-001",
        "task_version": 1,
        "category": "bug_fix",
        "risk_tier": "standard",
        "fixture_bundle": {
            "sha256": fixture.sha256,
            "file_count": fixture.file_count,
            "uncompressed_bytes": fixture.uncompressed_bytes,
        },
        "grader": {
            "sha256": grader.sha256,
            "runtime": "python3.12",
            "entrypoint": "grader/grade.py",
            "timeout_seconds": 60,
            "network_mode": "disabled",
        },
        "issue": {"title": "[Eval] Bounded task", "body": "## Goal\n\nRepair one bounded fixture."},
        "allowed_paths": ["src/example.py", "tests/**"],
        "prohibited_effects": ["No workflow changes", "No credential access"],
        "required_checks": ["CI", "Unit Tests"],
        "protected_authorization": None,
        "expected_completion_class": "change_required",
        "expected_human_action_reason": None,
        "trial_count": 2,
        "environment_profile": "ubuntu-24.04-python3.12-v1",
        "tags": ["bounded", "fixture"],
    }


def entry(manifest: bytes, task_id: str = "foundation.task-001") -> dict:
    return {
        "task_id": task_id,
        "task_version": 1,
        "manifest_path": f"tasks/{task_id}.json",
        "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
        "fixture_root": f"fixtures/{task_id}",
        "grader_root": f"graders/{task_id}",
    }


def catalog(entries: list[dict]) -> dict:
    return {
        "schema_version": 1,
        "suite_id": "foundation.initial",
        "suite_version": 1,
        "foundation_sha": "2" * 40,
        "task_count": len(entries),
        "tasks": entries,
    }


def make_suite(root: Path) -> tuple[bytes, bytes, dict]:
    fixture = root / "fixtures/foundation.task-001"
    grader = root / "graders/foundation.task-001/grader"
    tasks = root / "tasks"
    fixture.mkdir(parents=True)
    grader.mkdir(parents=True)
    tasks.mkdir()
    (fixture / "input.txt").write_text("fixture", encoding="utf-8")
    (grader / "grade.py").write_text(
        "from pathlib import Path\nPath('EXECUTED').write_text('bad')\n", encoding="utf-8"
    )
    fixture_id = suite.inspect_directory_bundle(fixture)
    grader_id = suite.inspect_directory_bundle(grader.parent)
    manifest = canonical(task_payload(fixture_id, grader_id))
    (tasks / "foundation.task-001.json").write_bytes(manifest)
    data = catalog([entry(manifest)])
    return canonical(data), manifest, data


class EvaluationSuiteContractTest(unittest.TestCase):
    def test_valid_suite_is_immutable_digest_bound_and_never_executes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw, _, _ = make_suite(root)
            loaded = suite.load_evaluation_suite(raw, root)
            self.assertEqual(loaded.catalog.suite_id, "foundation.initial")
            self.assertEqual(loaded.catalog.task_count, 1)
            self.assertEqual(loaded.catalog.catalog_sha256, hashlib.sha256(raw).hexdigest())
            self.assertFalse((root / "EXECUTED").exists())
            with self.assertRaisesRegex(Exception, "cannot assign"):
                loaded.catalog.suite_version = 2

    def test_catalog_requires_canonical_bounded_strict_json(self):
        raw = canonical(catalog([entry(b"manifest")]))
        suite.parse_evaluation_suite_catalog(raw)
        for invalid in (
            b"", b"[]", b"not-json", b"\xff", raw + b"\n",
            json.dumps(json.loads(raw), indent=2).encode(),
            b'{"schema_version":1,"schema_version":1}', b'{"x":NaN}',
            b"x" * (suite.MAX_CATALOG_BYTES + 1),
        ):
            with self.subTest(invalid=invalid[:30]):
                with self.assertRaises(suite.EvaluationSuiteError):
                    suite.parse_evaluation_suite_catalog(invalid)

    def test_catalog_keys_count_order_identity_and_paths_fail_closed(self):
        first = entry(b"one")
        second = entry(b"two", "foundation.task-002")
        cases = []
        data = catalog([first]); data["unknown"] = True; cases.append(data)
        data = catalog([first]); data["schema_version"] = 2; cases.append(data)
        data = catalog([first]); data["task_count"] = 2; cases.append(data)
        cases.append(catalog([second, first]))
        cases.append(catalog([first, first]))
        for key, value in (
            ("task_id", "Bad"),
            ("manifest_path", "other/task.json"),
            ("manifest_path", "tasks/.GIT/task.json"),
            ("manifest_path", "tasks/*.json"),
            ("fixture_root", "fixtures/../task"),
            ("grader_root", "C:/graders/task"),
        ):
            bad = dict(first); bad[key] = value; cases.append(catalog([bad]))
        duplicate = dict(second); duplicate["fixture_root"] = first["fixture_root"]
        cases.append(catalog([first, duplicate]))
        middle = dict(second); middle["fixture_root"] = first["fixture_root"] + "-peer"
        third = entry(b"three", "foundation.task-003")
        third["fixture_root"] = first["fixture_root"] + "/nested"
        cases.append(catalog([first, middle, third]))
        for data in cases:
            with self.subTest(data=data):
                with self.assertRaises(suite.EvaluationSuiteError):
                    suite.parse_evaluation_suite_catalog(canonical(data))

    def test_manifest_linkage_bundle_identity_and_entrypoint_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw, manifest, data = make_suite(root)
            mutations = []
            bad = json.loads(json.dumps(data)); bad["tasks"][0]["manifest_sha256"] = "0" * 64
            mutations.append((canonical(bad), None))
            bad_manifest = json.loads(manifest); bad_manifest["task_id"] = "foundation.other"
            mutations.append((raw, (root / "tasks/foundation.task-001.json", canonical(bad_manifest))))
            fixture_file = root / "fixtures/foundation.task-001/input.txt"
            mutations.append((raw, (fixture_file, b"changed")))
            grader_file = root / "graders/foundation.task-001/grader/grade.py"
            mutations.append((raw, (grader_file, b"changed")))
            for catalog_raw, change in mutations:
                original = None
                if change:
                    path, content = change; original = path.read_bytes(); path.write_bytes(content)
                with self.subTest(change=change):
                    with self.assertRaises(suite.EvaluationSuiteError):
                        suite.load_evaluation_suite(catalog_raw, root)
                if change:
                    path.write_bytes(original)
            grader_file.unlink()
            with self.assertRaises(suite.EvaluationSuiteError):
                suite.load_evaluation_suite(raw, root)

    def test_bundle_digest_binds_sorted_paths_content_size_and_executable(self):
        with tempfile.TemporaryDirectory() as left, tempfile.TemporaryDirectory() as right:
            a, b = Path(left), Path(right)
            for root, order in ((a, ("z.txt", "a.txt")), (b, ("a.txt", "z.txt"))):
                for name in order:
                    (root / name).write_text(name, encoding="utf-8")
            self.assertEqual(suite.inspect_directory_bundle(a), suite.inspect_directory_bundle(b))
            before = suite.inspect_directory_bundle(a).sha256
            os.chmod(a / "a.txt", 0o755)
            self.assertNotEqual(before, suite.inspect_directory_bundle(a).sha256)

    def test_bundle_rejects_symlink_hardlink_fifo_case_ambiguity_and_limits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "file.txt").write_text("x", encoding="utf-8")
            variants = []
            if hasattr(os, "symlink"):
                variants.append(lambda: os.symlink(root / "file.txt", root / "link.txt"))
            if hasattr(os, "link"):
                variants.append(lambda: os.link(root / "file.txt", root / "hard.txt"))
            if hasattr(os, "mkfifo"):
                variants.append(lambda: os.mkfifo(root / "pipe"))
            for create in variants:
                create()
                with self.assertRaises(suite.EvaluationSuiteError):
                    suite.inspect_directory_bundle(root)
                for child in root.iterdir():
                    if child.name != "file.txt": child.unlink()
            (root / "FILE.txt").write_text("y", encoding="utf-8")
            with self.assertRaises(suite.EvaluationSuiteError):
                suite.inspect_directory_bundle(root)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(suite.EvaluationSuiteError):
                suite.inspect_directory_bundle(root)
            (root / "large").write_bytes(b"xx")
            with mock.patch.object(suite, "MAX_FILE_BYTES", 1):
                with self.assertRaises(suite.EvaluationSuiteError):
                    suite.inspect_directory_bundle(root)

    def test_suite_and_referenced_symlinks_fail_closed(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unavailable")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw, _, _ = make_suite(root)
            alias = root.parent / (root.name + "-alias")
            os.symlink(root, alias, target_is_directory=True)
            try:
                with self.assertRaises(suite.EvaluationSuiteError):
                    suite.load_evaluation_suite(raw, alias)
            finally:
                alias.unlink()
            fixture = root / "fixtures/foundation.task-001"
            moved = root / "fixtures/real"
            fixture.rename(moved)
            os.symlink(moved, fixture, target_is_directory=True)
            with self.assertRaises(suite.EvaluationSuiteError):
                suite.load_evaluation_suite(raw, root)

    def test_public_schema_tracks_parser_keys_and_path_rules(self):
        schema = json.loads((ROOT / "docs/AGENT_EVAL_SUITE.schema.json").read_text())
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(suite.TOP_LEVEL_KEYS))
        self.assertEqual(
            set(schema["$defs"]["taskEntry"]["required"]), set(suite.TASK_ENTRY_KEYS)
        )
        patterns = {
            key: schema["$defs"][key]["pattern"]
            for key in ("manifestPath", "fixtureRoot", "graderRoot")
        }
        for key, valid in (
            ("manifestPath", "tasks/a/task.json"),
            ("fixtureRoot", "fixtures/a"),
            ("graderRoot", "graders/a"),
        ):
            self.assertIsNotNone(re.fullmatch(patterns[key], valid))
        for key, invalid in (
            ("manifestPath", "tasks/.GIT/task.json"),
            ("manifestPath", "tasks/*.json"),
            ("manifestPath", "tasks/control\n.json"),
            ("fixtureRoot", "fixtures/ leading"),
            ("graderRoot", "graders/trailing "),
        ):
            self.assertIsNone(re.fullmatch(patterns[key], invalid))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8", newline="\n")


def replace_once(content: str, old: str, new: str, label: str) -> str:
    count = content.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return content.replace(old, new, 1)


# Reserve Foundation-owned workflows from product aliases.
path = "scripts/foundation_product_checks.py"
content = read(path)
content = replace_once(
    content,
    'RESERVED_CHECK_NAMES = frozenset({"CI", "Unit Tests"})\n',
    'RESERVED_CHECK_NAMES = frozenset({"CI", "Unit Tests"})\nRESERVED_WORKFLOW_PATHS = frozenset({\n    ".github/workflows/ci.yml",\n    ".github/workflows/unit-tests.yml",\n    ".github/workflows/trusted-checks.yml",\n    ".github/workflows/claude-queue.yml",\n    ".github/workflows/claude-queue-comment-bridge.yml",\n    ".github/workflows/ci-reconcile.yml",\n    ".github/workflows/supervisor.yml",\n})\n',
    "reserved workflow paths",
)
content = replace_once(
    content,
    '            or "//" in workflow\n        ):\n',
    '            or "//" in workflow\n            or workflow in RESERVED_WORKFLOW_PATHS\n        ):\n',
    "reserved workflow rejection",
)
write(path, content)

# Bootstrap validates an existing target-owned product configuration before any write.
path = "bootstrap/generator.py"
content = read(path)
content = replace_once(content, "import subprocess\n", "import subprocess\nimport sys\n", "bootstrap sys import")
content = replace_once(
    content,
    'ROOT = Path(__file__).resolve().parents[1]\nGENERATED_TARGET_MARKER =',
    'ROOT = Path(__file__).resolve().parents[1]\nif str(ROOT) not in sys.path:\n    sys.path.insert(0, str(ROOT))\nfrom scripts.foundation_product_checks import ProductCheckConfigError, parse_product_checks\n\nGENERATED_TARGET_MARKER =',
    "bootstrap parser import",
)
content = replace_once(
    content,
    '            if target_owned:\n                action = "target-owned-unchanged" if target_digest == source_digest else "target-owned-preserved"\n                preserved.append(relative)\n',
    '            if target_owned:\n                try:\n                    parse_product_checks(destination.read_bytes())\n                except ProductCheckConfigError as exc:\n                    raise ValueError(f"target-owned product check configuration is invalid: {relative}") from exc\n                action = "target-owned-unchanged" if target_digest == source_digest else "target-owned-preserved"\n                preserved.append(relative)\n',
    "bootstrap target config validation",
)
write(path, content)

# Generated-target mode requires a real lock identity and absence of the source generator.
path = "scripts/public_export_guard.py"
content = read(path)
content = replace_once(content, "import argparse\n", "import argparse\nimport json\n", "export json import")
content = replace_once(
    content,
    'def is_generated_target(root: Path) -> bool:\n    checklist = root / "INSTALL_CHECKLIST.md"\n    return checklist.is_file() and GENERATED_TARGET_MARKER in checklist.read_text(encoding="utf-8")\n',
    'def is_generated_target(root: Path) -> bool:\n    checklist = root / "INSTALL_CHECKLIST.md"\n    lock_path = root / "FOUNDATION.lock.json"\n    source_generator = root / "bootstrap/generator.py"\n    if (\n        checklist.is_symlink()\n        or not checklist.is_file()\n        or lock_path.is_symlink()\n        or not lock_path.is_file()\n        or source_generator.exists()\n    ):\n        return False\n    if GENERATED_TARGET_MARKER not in checklist.read_text(encoding="utf-8"):\n        return False\n    try:\n        lock = json.loads(lock_path.read_text(encoding="utf-8"))\n    except (OSError, json.JSONDecodeError):\n        return False\n    return (\n        isinstance(lock, dict)\n        and lock.get("schema_version") == 1\n        and lock.get("source_repository") == "shiroku46/ai-dev-automation-foundation"\n        and re.fullmatch(r"[0-9a-f]{40}", str(lock.get("source_sha") or "")) is not None\n        and isinstance(lock.get("managed_files"), list)\n    )\n',
    "generated target identity",
)
write(path, content)

# Coordinator binds every candidate-configured future workflow to a candidate blob.
path = "scripts/github_coordinator_supervisor.py"
content = read(path)
content = replace_once(
    content,
    '    candidate_product_raw, _candidate_product_checks = _product_checks(client, head, "candidate")\n',
    '    candidate_product_raw, candidate_product_checks = _product_checks(client, head, "candidate")\n',
    "candidate checks variable",
)
content = replace_once(
    content,
    '    product_check_names = {item.name for item in default_product_checks}\n    pr_body = _text(pr.get("body"), "PR body")\n',
    '    product_check_names = {item.name for item in default_product_checks}\n    candidate_product_blobs = tuple(\n        (item.workflow, client.file_blob(item.workflow, head))\n        for item in candidate_product_checks\n    )\n    pr_body = _text(pr.get("body"), "PR body")\n',
    "candidate workflow snapshot",
)
content = replace_once(
    content,
    '    if client.file_content(PRODUCT_CHECKS_PATH, head) != candidate_product_raw:\n        raise SupervisorError("candidate product check configuration changed during evaluation")\n',
    '    if client.file_content(PRODUCT_CHECKS_PATH, head) != candidate_product_raw:\n        raise SupervisorError("candidate product check configuration changed during evaluation")\n    if tuple(\n        (item.workflow, client.file_blob(item.workflow, head))\n        for item in candidate_product_checks\n    ) != candidate_product_blobs:\n        raise SupervisorError("candidate product workflow definitions changed during evaluation")\n',
    "candidate workflow race",
)
write(path, content)

# Parser negative coverage.
path = "tests/test_foundation_product_checks.py"
content = read(path)
content = replace_once(
    content,
    '            [{"name": "A", "workflow": ".github/workflows/a.json"}],\n',
    '            [{"name": "A", "workflow": ".github/workflows/a.json"}],\n            [{"name": "Alias CI", "workflow": ".github/workflows/ci.yml"}],\n            [{"name": "Alias Supervisor", "workflow": ".github/workflows/supervisor.yml"}],\n',
    "reserved workflow tests",
)
write(path, content)

# Coordinator fake supports missing/racing workflow blobs and candidate-only workflow coverage.
path = "tests/test_github_coordinator_supervisor.py"
content = read(path)
content = replace_once(
    content,
    '        self.issue_reads = self.pr_reads = self.run_reads = self.thread_reads = self.content_reads = 0\n        self.issue_race = self.pr_race = self.run_race = self.thread_race = self.content_race = None\n',
    '        self.issue_reads = self.pr_reads = self.run_reads = self.thread_reads = self.content_reads = self.blob_reads = 0\n        self.issue_race = self.pr_race = self.run_race = self.thread_race = self.content_race = self.blob_race = None\n',
    "fake blob counters",
)
content = replace_once(
    content,
    '    def file_blob(self, path, ref): return self.blobs[(path, ref)]\n',
    '    def file_blob(self, path, ref):\n        self.blob_reads += 1\n        if self.blob_reads == 5 and self.blob_race: self.blob_race(self)\n        try:\n            return self.blobs[(path, ref)]\n        except KeyError as exc:\n            raise SupervisorError("file blob is missing") from exc\n',
    "fake blob method",
)
content = replace_once(
    content,
    '        self.issue_reads = self.pr_reads = self.run_reads = self.thread_reads = self.content_reads = 0\n',
    '        self.issue_reads = self.pr_reads = self.run_reads = self.thread_reads = self.content_reads = self.blob_reads = 0\n',
    "ready blob reset",
)
content = replace_once(
    content,
    '        client.contents[(PRODUCT_CHECKS_PATH, HEAD_SHA)] = future\n        self.assertEqual(evaluate(client, REPO, 5).action, "merge")\n',
    '        client.contents[(PRODUCT_CHECKS_PATH, HEAD_SHA)] = future\n        client.blobs[(".github/workflows/future.yml", HEAD_SHA)] = "9" * 40\n        self.assertEqual(evaluate(client, REPO, 5).action, "merge")\n        missing = FakeClient()\n        missing.contents[(PRODUCT_CHECKS_PATH, HEAD_SHA)] = future\n        with self.assertRaisesRegex(SupervisorError, "file blob is missing"):\n            evaluate(missing, REPO, 5)\n',
    "candidate future workflow test",
)
content = replace_once(
    content,
    '        with self.assertRaisesRegex(SupervisorError, "configuration changed"):\n            evaluate(client, REPO, 5)\n',
    '        with self.assertRaisesRegex(SupervisorError, "configuration changed"):\n            evaluate(client, REPO, 5)\n        client = FakeClient()\n        future = json.dumps({\n            "schema_version": 1,\n            "checks": [{"name": "Future Check", "workflow": ".github/workflows/future.yml"}],\n        }).encode()\n        client.contents[(PRODUCT_CHECKS_PATH, HEAD_SHA)] = future\n        client.blobs[(".github/workflows/future.yml", HEAD_SHA)] = "9" * 40\n        client.blob_race = lambda value: value.blobs.__setitem__(\n            (".github/workflows/future.yml", HEAD_SHA), "8" * 40\n        )\n        with self.assertRaisesRegex(SupervisorError, "candidate product workflow definitions changed"):\n            evaluate(client, REPO, 5)\n',
    "candidate blob race test",
)
write(path, content)

# Export mode cannot be enabled by a marker alone.
path = "tests/test_export_guard.py"
content = read(path)
content = replace_once(content, "import tempfile\n", "import json\nimport tempfile\n", "export test json")
content = replace_once(
    content,
    '            (root / "INSTALL_CHECKLIST.md").write_text(GENERATED_TARGET_MARKER + "\\n")\n            self.assertEqual(scan(root), [])\n',
    '            (root / "INSTALL_CHECKLIST.md").write_text(GENERATED_TARGET_MARKER + "\\n")\n            self.assertTrue(any("product-specific" in item for item in scan(root)))\n            (root / "FOUNDATION.lock.json").write_text(json.dumps({\n                "schema_version": 1,\n                "source_repository": "shiroku46/ai-dev-automation-foundation",\n                "source_sha": "a" * 40,\n                "managed_files": [],\n            }))\n            self.assertEqual(scan(root), [])\n',
    "export generated identity test",
)
source_test = '''
    def test_source_generator_prevents_generated_mode_spoofing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("TRPG product")
            (root / "INSTALL_CHECKLIST.md").write_text(GENERATED_TARGET_MARKER + "\\n")
            (root / "FOUNDATION.lock.json").write_text(json.dumps({
                "schema_version": 1,
                "source_repository": "shiroku46/ai-dev-automation-foundation",
                "source_sha": "a" * 40,
                "managed_files": [],
            }))
            generator = root / "bootstrap/generator.py"
            generator.parent.mkdir(parents=True)
            generator.write_text("source generator")
            self.assertTrue(any("product-specific" in item for item in scan(root)))
'''
content = replace_once(
    content,
    '\n\nif __name__ == "__main__":\n',
    source_test + '\n\nif __name__ == "__main__":\n',
    "export source spoof test",
)
write(path, content)

# Bootstrap malformed existing target config aborts before any Foundation write.
path = "tests/test_bootstrap.py"
content = read(path)
bootstrap_test = '''
    def test_malformed_existing_product_check_config_aborts_before_write(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            config = target / ".github/foundation-product-checks.json"
            config.parent.mkdir(parents=True)
            config.write_text("not-json", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "product check configuration"):
                render(
                    target,
                    "owner",
                    mode="existing-product",
                    source_sha=SOURCE_SHA,
                    installed_at=INSTALLED_AT,
                )
            self.assertEqual(config.read_text(encoding="utf-8"), "not-json")
            self.assertFalse((target / "scripts").exists())
            self.assertFalse((target / LOCK_FILE).exists())
'''
content = replace_once(
    content,
    '    def test_product_check_config_is_target_owned_and_preserved(self):\n',
    bootstrap_test + '\n    def test_product_check_config_is_target_owned_and_preserved(self):\n',
    "bootstrap malformed config test",
)
write(path, content)

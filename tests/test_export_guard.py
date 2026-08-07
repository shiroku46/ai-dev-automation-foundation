import json
import tempfile
import unittest
from pathlib import Path
from scripts.public_export_guard import GENERATED_TARGET_MARKER, scan


class ExportGuardTest(unittest.TestCase):
    def test_clean_and_secret_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("public content")
            self.assertEqual(scan(root), [])
            (root / "bad.txt").write_text("api_key = abcdefghijklmnopqrstuvwxyz")
            self.assertTrue(scan(root))

    def test_product_terms_are_source_only_restrictions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("TRPG BOOTH product")
            self.assertTrue(any("product-specific" in item for item in scan(root)))
            (root / "INSTALL_CHECKLIST.md").write_text(GENERATED_TARGET_MARKER + "\n")
            self.assertTrue(any("product-specific" in item for item in scan(root)))
            (root / "FOUNDATION.lock.json").write_text(json.dumps({
                "schema_version": 1,
                "source_repository": "shiroku46/ai-dev-automation-foundation",
                "source_sha": "a" * 40,
                "managed_files": [],
            }))
            self.assertEqual(scan(root), [])
            (root / "secret.txt").write_text("access_token = abcdefghijklmnopqrstuvwxyz")
            self.assertTrue(any("credential-value" in item for item in scan(root)))

    def test_source_generator_prevents_generated_mode_spoofing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("TRPG product")
            (root / "INSTALL_CHECKLIST.md").write_text(GENERATED_TARGET_MARKER + "\n")
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

    def test_dependency_and_generated_trees_are_ignored(self):
        ignored_parts = (
            "node_modules",
            ".next",
            "test-results",
            "playwright-report",
            "coverage",
            ".turbo",
        )
        sensitive_value = "api" + "_key = " + "Z" * 24
        for ignored in ignored_parts:
            with self.subTest(ignored=ignored):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    generated = root / ignored / "nested" / "generated.md"
                    generated.parent.mkdir(parents=True)
                    generated.write_text(sensitive_value)
                    self.assertEqual(scan(root), [])

    def test_repository_owned_source_remains_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src" / "config.txt"
            source.parent.mkdir(parents=True)
            source.write_text("api" + "_key = " + "Z" * 24)
            findings = scan(root)
            self.assertTrue(
                any(
                    "src/config.txt" in finding and "credential-value" in finding
                    for finding in findings
                )
            )

    def test_ignored_tree_name_in_filename_is_still_scanned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dependency = root / "node_modules" / "dependency.md"
            dependency.parent.mkdir(parents=True)
            dependency.write_text("api" + "_key = " + "Z" * 24)
            note = root / "docs" / "node_modules-notes.md"
            note.parent.mkdir(parents=True)
            note.write_text("api" + "_key = " + "Z" * 24)
            findings = scan(root)
            self.assertFalse(any("node_modules/dependency.md" in item for item in findings))
            self.assertTrue(
                any(
                    "docs/node_modules-notes.md" in item
                    and "credential-value" in item
                    for item in findings
                )
            )


if __name__ == "__main__":
    unittest.main()
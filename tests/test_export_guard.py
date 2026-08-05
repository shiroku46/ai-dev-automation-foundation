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


if __name__ == "__main__":
    unittest.main()

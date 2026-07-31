import tempfile
import unittest
from pathlib import Path
from scripts.public_export_guard import scan

class ExportGuardTest(unittest.TestCase):
    def test_clean_and_secret_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("public content")
            self.assertEqual(scan(root), [])
            (root / "bad.txt").write_text("api_key = abcdefghijklmnopqrstuvwxyz")
            self.assertTrue(scan(root))

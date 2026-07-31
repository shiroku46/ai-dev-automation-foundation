import tempfile
import unittest
from pathlib import Path
from bootstrap.generator import ALLOWLIST, render

class BootstrapTest(unittest.TestCase):
    def test_rendered_allowlist(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render(target, "example-owner")
            for path in ALLOWLIST:
                self.assertTrue((target / path).is_file(), path)
            checklist = (target / "INSTALL_CHECKLIST.md").read_text()
            self.assertIn("example-owner", checklist)
            self.assertNotIn("notion", checklist.lower())

import unittest

from src.resume import checkpoint


class ResumeTests(unittest.TestCase):
    def test_resume_checkpoint_reaches_ready(self):
        self.assertEqual(checkpoint(), "ready")


if __name__ == "__main__":
    unittest.main()

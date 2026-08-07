import unittest

from src.resume import checkpoint


class ResumeTests(unittest.TestCase):
    def test_resume_checkpoint_reaches_done(self):
        self.assertEqual(checkpoint(), "done")


if __name__ == "__main__":
    unittest.main()

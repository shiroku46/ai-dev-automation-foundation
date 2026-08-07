import unittest

from src.label import normalize_label


class LabelTests(unittest.TestCase):
    def test_repeated_spaces_collapse(self):
        self.assertEqual(normalize_label("  alpha   beta  "), "alpha beta")


if __name__ == "__main__":
    unittest.main()

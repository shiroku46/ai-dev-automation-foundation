import unittest

from src.code import normalize_code


class NormalizeCodeTests(unittest.TestCase):
    def test_outer_spaces_are_removed(self):
        self.assertEqual(normalize_code(" abcd "), "ABCD")


if __name__ == "__main__":
    unittest.main()

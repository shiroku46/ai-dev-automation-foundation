import unittest

from src.display import display_name
from src.text import normalize


class DisplayTests(unittest.TestCase):
    def test_normalize_collapses_internal_whitespace(self):
        self.assertEqual(normalize("  alpha   beta  "), "alpha beta")

    def test_display_name_uses_normalized_title_case(self):
        self.assertEqual(display_name("  alpha   beta  "), "Alpha Beta")


if __name__ == "__main__":
    unittest.main()

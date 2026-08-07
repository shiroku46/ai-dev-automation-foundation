import unittest

from src.formatter import headline


class HeadlineTests(unittest.TestCase):
    def test_words_are_title_cased(self):
        self.assertEqual(headline("red fox"), "Red Fox")

    def test_outer_whitespace_is_removed(self):
        self.assertEqual(headline("  blue moon  "), "Blue Moon")


if __name__ == "__main__":
    unittest.main()

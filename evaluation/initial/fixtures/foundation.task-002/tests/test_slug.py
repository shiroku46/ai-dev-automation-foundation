import unittest

from src.slug import slugify


class SlugifyTests(unittest.TestCase):
    def test_spaces_become_separators(self):
        self.assertEqual(slugify("Alpha Beta"), "alpha-beta")


if __name__ == "__main__":
    unittest.main()

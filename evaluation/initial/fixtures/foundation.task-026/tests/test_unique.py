import unittest

from src.unique import unique


class UniqueTests(unittest.TestCase):
    def test_duplicates_are_removed(self):
        self.assertEqual(unique(["b", "a", "b", "c", "a"]), ["b", "a", "c"])

    def test_first_occurrence_order_is_preserved(self):
        self.assertEqual(unique(["z", "x", "z", "y", "x"]), ["z", "x", "y"])


if __name__ == "__main__":
    unittest.main()

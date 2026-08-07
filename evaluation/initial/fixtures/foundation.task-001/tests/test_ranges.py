import unittest

from src.ranges import clamp


class ClampTests(unittest.TestCase):
    def test_value_inside_interval_is_unchanged(self):
        self.assertEqual(clamp(5, 0, 10), 5)

    def test_value_below_interval_uses_lower_bound(self):
        self.assertEqual(clamp(-2, 0, 10), 0)

    def test_value_above_interval_uses_upper_bound(self):
        self.assertEqual(clamp(12, 0, 10), 10)

    def test_invalid_interval_is_rejected(self):
        with self.assertRaises(ValueError):
            clamp(5, 10, 0)


if __name__ == "__main__":
    unittest.main()

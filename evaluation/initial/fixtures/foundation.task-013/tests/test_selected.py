import unittest

from src.selected import selected_value


class CollisionTests(unittest.TestCase):
    def test_selected_path_is_fixed(self):
        self.assertEqual(selected_value(), "right")


if __name__ == "__main__":
    unittest.main()

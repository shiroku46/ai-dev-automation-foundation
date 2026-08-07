import unittest

from src.window import in_window


class WindowTests(unittest.TestCase):
    def test_start_boundary_is_included(self):
        self.assertTrue(in_window(10, 10, 20))

    def test_end_boundary_is_included(self):
        self.assertTrue(in_window(20, 10, 20))

    def test_inside_value_is_included(self):
        self.assertTrue(in_window(15, 10, 20))

    def test_outside_value_is_rejected(self):
        self.assertFalse(in_window(21, 10, 20))


if __name__ == "__main__":
    unittest.main()

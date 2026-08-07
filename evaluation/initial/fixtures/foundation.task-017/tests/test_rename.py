import unittest

from src.new_name import value


class RenameTests(unittest.TestCase):
    def test_new_module_exposes_value(self):
        self.assertEqual(value(), "ready")


if __name__ == "__main__":
    unittest.main()

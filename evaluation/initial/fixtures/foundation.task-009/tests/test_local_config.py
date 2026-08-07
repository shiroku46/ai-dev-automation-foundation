import unittest

from src.local_config import local_feature_enabled


class LocalConfigTests(unittest.TestCase):
    def test_local_feature_is_enabled(self):
        self.assertTrue(local_feature_enabled())


if __name__ == "__main__":
    unittest.main()

import unittest

from src.settings import retry_count


class RetryCountTests(unittest.TestCase):
    def test_retry_override(self):
        self.assertEqual(retry_count({"retries": 5}), 5)

    def test_retry_default(self):
        self.assertEqual(retry_count({}), 2)


if __name__ == "__main__":
    unittest.main()

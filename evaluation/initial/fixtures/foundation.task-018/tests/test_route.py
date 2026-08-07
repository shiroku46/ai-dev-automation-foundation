import unittest

from src.route import route


class ProviderQuotaTests(unittest.TestCase):
    def test_provider_with_quota_is_used(self):
        self.assertEqual(route(True), "provider")

    def test_exhausted_optional_provider_falls_back_to_github_direct(self):
        self.assertEqual(route(False), "github-direct")


if __name__ == "__main__":
    unittest.main()

import unittest

from src.route import route


class ProviderTimeoutTests(unittest.TestCase):
    def test_available_provider_is_used(self):
        self.assertEqual(route("available"), "provider")

    def test_timed_out_optional_provider_falls_back_to_github_direct(self):
        self.assertEqual(route("timeout"), "github-direct")

    def test_unavailable_optional_provider_falls_back_to_github_direct(self):
        self.assertEqual(route("unavailable"), "github-direct")


if __name__ == "__main__":
    unittest.main()

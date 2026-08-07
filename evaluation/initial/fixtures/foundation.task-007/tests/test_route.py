import unittest

from src.route import implementation_route


class ImplementationRouteTests(unittest.TestCase):
    def test_available_provider_uses_provider_route(self):
        self.assertEqual(implementation_route(True), "provider")

    def test_unavailable_provider_continues_github_direct(self):
        self.assertEqual(implementation_route(False), "github-direct")


if __name__ == "__main__":
    unittest.main()

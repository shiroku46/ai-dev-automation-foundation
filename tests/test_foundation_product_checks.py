"""Bounded product workflow configuration tests."""
from __future__ import annotations

import json
import unittest

from scripts.foundation_product_checks import (
    MAX_CONFIG_BYTES,
    ProductCheck,
    ProductCheckConfigError,
    parse_product_checks,
)


class ProductCheckConfigTest(unittest.TestCase):
    def payload(self, checks):
        return json.dumps({"schema_version": 1, "checks": checks})

    def test_empty_and_valid_checks_are_deterministic(self):
        self.assertEqual(parse_product_checks(self.payload([])), ())
        parsed = parse_product_checks(self.payload([
            {"name": "Type Check", "workflow": ".github/workflows/type-check.yaml"},
            {"name": "Product CI", "workflow": ".github/workflows/product-ci.yml"},
        ]))
        self.assertEqual(parsed, (
            ProductCheck("Product CI", ".github/workflows/product-ci.yml"),
            ProductCheck("Type Check", ".github/workflows/type-check.yaml"),
        ))

    def test_malformed_schema_and_object_keys_fail_closed(self):
        for content in (
            "", "not-json", "[]",
            json.dumps({"schema_version": 2, "checks": []}),
            json.dumps({"schema_version": 1}),
            json.dumps({"schema_version": 1, "checks": [], "extra": True}),
            json.dumps({"schema_version": 1, "checks": {}}),
        ):
            with self.subTest(content=content):
                with self.assertRaises(ProductCheckConfigError):
                    parse_product_checks(content)

    def test_oversized_and_excessive_configuration_fails(self):
        with self.assertRaises(ProductCheckConfigError):
            parse_product_checks(b"x" * (MAX_CONFIG_BYTES + 1))
        checks = [
            {"name": f"Check {index}", "workflow": f".github/workflows/check-{index}.yml"}
            for index in range(21)
        ]
        with self.assertRaises(ProductCheckConfigError):
            parse_product_checks(self.payload(checks))

    def test_duplicate_reserved_and_unsafe_identities_fail(self):
        invalid_sets = (
            [
                {"name": "Product CI", "workflow": ".github/workflows/a.yml"},
                {"name": "product ci", "workflow": ".github/workflows/b.yml"},
            ],
            [
                {"name": "A", "workflow": ".github/workflows/a.yml"},
                {"name": "B", "workflow": ".github/workflows/a.yml"},
            ],
            [{"name": "CI", "workflow": ".github/workflows/a.yml"}],
            [{"name": "Unit Tests", "workflow": ".github/workflows/a.yml"}],
            [{"name": "A", "workflow": "product-ci.yml"}],
            [{"name": "A", "workflow": ".github/workflows/../ci.yml"}],
            [{"name": "A\nB", "workflow": ".github/workflows/a.yml"}],
            [{"name": "A", "workflow": ".github/workflows/a.json"}],
            [{"name": "Alias CI", "workflow": ".github/workflows/ci.yml"}],
            [{"name": "Alias Supervisor", "workflow": ".github/workflows/supervisor.yml"}],
            [{"name": "A", "workflow": ".github/workflows/a.yml", "extra": 1}],
        )
        for checks in invalid_sets:
            with self.subTest(checks=checks):
                with self.assertRaises(ProductCheckConfigError):
                    parse_product_checks(self.payload(checks))


if __name__ == "__main__":
    unittest.main()

"""Execution validation configuration tests."""
from __future__ import annotations

import json
import unittest

from scripts.foundation_product_checks import (
    ExternalCheck,
    MAX_CONFIG_BYTES,
    ProductCheck,
    ProductCheckConfigError,
    parse_external_checks,
    parse_product_checks,
    parse_validation_config,
)


class ProductCheckConfigTest(unittest.TestCase):
    def legacy(self, checks):
        return json.dumps({"schema_version": 1, "checks": checks})

    def v2(self, profile, checks):
        return json.dumps(
            {
                "schema_version": 2,
                "execution_profile": profile,
                "checks": checks,
            }
        )

    def test_legacy_schema_remains_deterministic(self):
        self.assertEqual(parse_product_checks(self.legacy([])), ())
        parsed = parse_product_checks(
            self.legacy(
                [
                    {"name": "Type Check", "workflow": ".github/workflows/type-check.yaml"},
                    {"name": "Product CI", "workflow": ".github/workflows/product-ci.yml"},
                ]
            )
        )
        self.assertEqual(
            parsed,
            (
                ProductCheck("Product CI", ".github/workflows/product-ci.yml"),
                ProductCheck("Type Check", ".github/workflows/type-check.yaml"),
            ),
        )
        config = parse_validation_config(self.legacy([]))
        self.assertEqual(config.execution_profile, "github-actions")
        self.assertEqual(config.schema_version, 1)

    def test_free_only_external_configuration(self):
        raw = self.v2(
            "free-only",
            [
                {
                    "kind": "external",
                    "name": "Cloudflare validation",
                    "provider": "cloudflare-workers-builds",
                    "check_name": "Workers Builds: app",
                    "app_slug": "cloudflare-workers-and-pages",
                    "app_id": 85455,
                }
            ],
        )
        config = parse_validation_config(raw)
        self.assertEqual(config.execution_profile, "free-only")
        self.assertEqual(config.workflow_checks, ())
        self.assertEqual(
            config.external_checks,
            (
                ExternalCheck(
                    "Cloudflare validation",
                    "cloudflare-workers-builds",
                    "Workers Builds: app",
                    "cloudflare-workers-and-pages",
                    85455,
                ),
            ),
        )
        self.assertEqual(parse_product_checks(raw), ())
        self.assertEqual(parse_external_checks(raw), config.external_checks)

    def test_v2_hybrid_github_actions_profile_is_supported(self):
        raw = self.v2(
            "github-actions",
            [
                {
                    "kind": "workflow",
                    "name": "Product CI",
                    "workflow": ".github/workflows/product-ci.yml",
                },
                {
                    "kind": "external",
                    "name": "External audit",
                    "provider": "cloudflare-workers-builds",
                    "check_name": "Workers Builds: app",
                    "app_slug": "cloudflare-workers-and-pages",
                },
            ],
        )
        config = parse_validation_config(raw)
        self.assertEqual(len(config.workflow_checks), 1)
        self.assertEqual(len(config.external_checks), 1)

    def test_free_only_rejects_workflow_dependency_or_no_external_evidence(self):
        for checks in (
            [],
            [
                {
                    "kind": "workflow",
                    "name": "Product CI",
                    "workflow": ".github/workflows/product-ci.yml",
                }
            ],
            [
                {
                    "kind": "workflow",
                    "name": "Product CI",
                    "workflow": ".github/workflows/product-ci.yml",
                },
                {
                    "kind": "external",
                    "name": "Cloudflare",
                    "provider": "cloudflare-workers-builds",
                    "check_name": "Workers Builds: app",
                    "app_slug": "cloudflare-workers-and-pages",
                },
            ],
        ):
            with self.subTest(checks=checks):
                with self.assertRaises(ProductCheckConfigError):
                    parse_validation_config(self.v2("free-only", checks))

    def test_malformed_schema_and_object_keys_fail_closed(self):
        invalid = (
            "",
            "not-json",
            "[]",
            json.dumps({"schema_version": True, "checks": []}),
            json.dumps({"schema_version": 3, "checks": []}),
            json.dumps({"schema_version": 1}),
            json.dumps({"schema_version": 1, "checks": [], "extra": True}),
            json.dumps({"schema_version": 2, "execution_profile": "free-only", "checks": [], "extra": True}),
            json.dumps({"schema_version": 2, "execution_profile": "paid", "checks": []}),
            json.dumps({"schema_version": 1, "checks": {}}),
        )
        for content in invalid:
            with self.subTest(content=content):
                with self.assertRaises(ProductCheckConfigError):
                    parse_validation_config(content)

    def test_oversized_and_excessive_configuration_fails(self):
        with self.assertRaises(ProductCheckConfigError):
            parse_validation_config(b"x" * (MAX_CONFIG_BYTES + 1))
        checks = [
            {
                "kind": "workflow",
                "name": f"Check {index}",
                "workflow": f".github/workflows/check-{index}.yml",
            }
            for index in range(21)
        ]
        with self.assertRaises(ProductCheckConfigError):
            parse_validation_config(self.v2("github-actions", checks))

    def test_duplicate_reserved_and_unsafe_workflows_fail(self):
        invalid_sets = (
            [
                {"kind": "workflow", "name": "Product CI", "workflow": ".github/workflows/a.yml"},
                {"kind": "workflow", "name": "product ci", "workflow": ".github/workflows/b.yml"},
            ],
            [
                {"kind": "workflow", "name": "A", "workflow": ".github/workflows/a.yml"},
                {"kind": "workflow", "name": "B", "workflow": ".github/workflows/a.yml"},
            ],
            [{"kind": "workflow", "name": "CI", "workflow": ".github/workflows/a.yml"}],
            [{"kind": "workflow", "name": "A", "workflow": "product-ci.yml"}],
            [{"kind": "workflow", "name": "A", "workflow": ".github/workflows/../ci.yml"}],
            [{"kind": "workflow", "name": "A\nB", "workflow": ".github/workflows/a.yml"}],
            [{"kind": "workflow", "name": "Alias CI", "workflow": ".github/workflows/ci.yml"}],
        )
        for checks in invalid_sets:
            with self.subTest(checks=checks):
                with self.assertRaises(ProductCheckConfigError):
                    parse_validation_config(self.v2("github-actions", checks))

    def test_external_identity_is_strict_and_unique(self):
        base = {
            "kind": "external",
            "name": "Cloudflare",
            "provider": "cloudflare-workers-builds",
            "check_name": "Workers Builds: app",
            "app_slug": "cloudflare-workers-and-pages",
            "app_id": 85455,
        }
        invalid = [
            {**base, "provider": "Cloudflare Workers Builds"},
            {**base, "app_slug": "Bad Slug"},
            {**base, "app_id": True},
            {**base, "app_id": 0},
            {**base, "extra": 1},
        ]
        for item in invalid:
            with self.subTest(item=item):
                with self.assertRaises(ProductCheckConfigError):
                    parse_validation_config(self.v2("free-only", [item]))
        duplicate = [base, {**base, "name": "Second"}]
        with self.assertRaisesRegex(ProductCheckConfigError, "duplicate external"):
            parse_validation_config(self.v2("free-only", duplicate))


if __name__ == "__main__":
    unittest.main()

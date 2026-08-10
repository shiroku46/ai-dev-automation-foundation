"""Tests for provider-neutral exact-head external CI evidence."""
from __future__ import annotations

import unittest

from scripts.external_validation import (
    ExternalCheckRequirement,
    ExternalValidationError,
    external_checks_satisfied,
    snapshot_external_checks,
)

HEAD = "b" * 40
REQUIREMENT = ExternalCheckRequirement(
    provider="cloudflare-workers-builds",
    name="Cloudflare Workers Builds",
    app_slug="cloudflare-workers-and-pages",
    app_id=1234,
)


def run(
    run_id: int,
    *,
    head: str = HEAD,
    pr: int = 7,
    name: str = "Cloudflare Workers Builds",
    slug: str = "cloudflare-workers-and-pages",
    app_id: int = 1234,
    status: str = "completed",
    conclusion: str | None = "success",
):
    return {
        "id": run_id,
        "name": name,
        "head_sha": head,
        "status": status,
        "conclusion": conclusion,
        "app": {"slug": slug, "id": app_id},
        "pull_requests": [{"number": pr}],
    }


class ExternalValidationTest(unittest.TestCase):
    def test_exact_identity_success_passes(self):
        evidence = snapshot_external_checks(
            [run(10)],
            head_sha=HEAD,
            pr_number=7,
            requirements=[REQUIREMENT],
        )
        self.assertTrue(external_checks_satisfied(evidence))
        self.assertEqual(evidence[0].run_id, 10)

    def test_wrong_sha_pr_app_or_name_is_missing(self):
        mutations = [
            run(10, head="c" * 40),
            run(10, pr=8),
            run(10, slug="github-actions"),
            run(10, app_id=9999),
            run(10, name="CI"),
        ]
        for candidate in mutations:
            with self.subTest(candidate=candidate):
                evidence = snapshot_external_checks(
                    [candidate],
                    head_sha=HEAD,
                    pr_number=7,
                    requirements=[REQUIREMENT],
                )
                self.assertEqual(evidence[0].state, "missing")
                self.assertFalse(external_checks_satisfied(evidence))

    def test_newer_pending_or_failure_supersedes_stale_success(self):
        for newer in [
            run(11, status="in_progress", conclusion=None),
            run(11, conclusion="failure"),
        ]:
            with self.subTest(newer=newer):
                evidence = snapshot_external_checks(
                    [run(10), newer],
                    head_sha=HEAD,
                    pr_number=7,
                    requirements=[REQUIREMENT],
                )
                self.assertEqual(evidence[0].run_id, 11)
                self.assertNotEqual(evidence[0].state, "success")
                self.assertFalse(external_checks_satisfied(evidence))

    def test_unassociated_commit_check_cannot_authorize_pr(self):
        candidate = run(10)
        candidate["pull_requests"] = []
        evidence = snapshot_external_checks(
            [candidate],
            head_sha=HEAD,
            pr_number=7,
            requirements=[REQUIREMENT],
        )
        self.assertEqual(evidence[0].state, "missing")

    def test_github_actions_cannot_impersonate_external_validator(self):
        candidate = run(10, slug="github-actions", app_id=15368)
        evidence = snapshot_external_checks(
            [candidate],
            head_sha=HEAD,
            pr_number=7,
            requirements=[REQUIREMENT],
        )
        self.assertEqual(evidence[0].state, "missing")

    def test_duplicate_requirement_and_malformed_identity_fail_closed(self):
        with self.assertRaisesRegex(ExternalValidationError, "duplicate"):
            snapshot_external_checks(
                [],
                head_sha=HEAD,
                pr_number=7,
                requirements=[REQUIREMENT, REQUIREMENT],
            )
        with self.assertRaisesRegex(ExternalValidationError, "head SHA"):
            snapshot_external_checks(
                [],
                head_sha="main",
                pr_number=7,
                requirements=[REQUIREMENT],
            )
        with self.assertRaisesRegex(ExternalValidationError, "check run id"):
            bad = run(10)
            bad["id"] = True
            snapshot_external_checks(
                [bad],
                head_sha=HEAD,
                pr_number=7,
                requirements=[REQUIREMENT],
            )

    def test_optional_app_id_still_records_observed_exact_app(self):
        requirement = ExternalCheckRequirement(
            provider="cloudflare-workers-builds",
            name="Cloudflare Workers Builds",
            app_slug="cloudflare-workers-and-pages",
        )
        evidence = snapshot_external_checks(
            [run(10)],
            head_sha=HEAD,
            pr_number=7,
            requirements=[requirement],
        )
        self.assertEqual(evidence[0].app_id, 1234)
        self.assertTrue(external_checks_satisfied(evidence))


if __name__ == "__main__":
    unittest.main()

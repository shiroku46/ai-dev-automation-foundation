"""Focused tests for the free-only external-validation coordinator."""
from __future__ import annotations

import copy
import json
import unittest
from typing import Any

from scripts.foundation_product_checks import CONFIG_PATH
from scripts.free_only_coordinator import (
    evaluate_free_only,
    supervise_free_only,
)
from scripts.github_coordinator_supervisor import SupervisorError

REPO = "owner/product"
DEFAULT_SHA = "a" * 40
HEAD_SHA = "b" * 40


def free_config(check_name: str = "Workers Builds: product") -> bytes:
    return json.dumps(
        {
            "schema_version": 2,
            "execution_profile": "free-only",
            "checks": [
                {
                    "kind": "external",
                    "name": "Cloudflare validation",
                    "provider": "cloudflare-workers-builds",
                    "check_name": check_name,
                    "app_slug": "cloudflare-workers-and-pages",
                    "app_id": 85455,
                }
            ],
        },
        sort_keys=True,
    ).encode()


def review() -> dict[str, Any]:
    return {
        "id": 10,
        "body": (
            f"<!-- foundation-coordinator-review:{HEAD_SHA}:clean -->\n"
            "Reviewed exact scope, external validation evidence, and expected-head merge boundary."
        ),
        "created_at": "2026-08-10T00:00:10Z",
        "updated_at": "2026-08-10T00:00:10Z",
        "user": {"login": "owner"},
    }


def external_run(
    run_id: int = 100,
    *,
    name: str = "Workers Builds: product",
    sha: str = HEAD_SHA,
    pr: int = 5,
    slug: str = "cloudflare-workers-and-pages",
    app_id: int = 85455,
    status: str = "completed",
    conclusion: str | None = "success",
) -> dict[str, Any]:
    return {
        "id": run_id,
        "name": name,
        "head_sha": sha,
        "status": status,
        "conclusion": conclusion,
        "started_at": f"2026-08-10T00:01:{run_id % 60:02d}Z",
        "app": {"slug": slug, "id": app_id},
        "pull_requests": [{"number": pr}],
    }


class FakeClient:
    def __init__(self, *, draft: bool = False):
        self.repo = {
            "full_name": REPO,
            "default_branch": "main",
            "owner": {"login": "owner"},
        }
        self.default_sha = DEFAULT_SHA
        self.pr = {
            "number": 5,
            "state": "open",
            "draft": draft,
            "mergeable": True,
            "node_id": "PR_node_5",
            "body": "Closes #7.\n",
            "labels": [],
            "head": {"sha": HEAD_SHA, "ref": "feature", "repo": {"full_name": REPO}},
            "base": {"ref": "main", "repo": {"full_name": REPO}},
        }
        self.source = {
            "number": 7,
            "state": "open",
            "body": "## Exact allowed paths\n- `scripts/app.py`\n",
            "updated_at": "2026-08-10T00:00:00Z",
            "user": {"login": "owner"},
        }
        self.files = {5: [{"filename": "scripts/app.py"}]}
        self.source_comments: list[dict[str, Any]] = []
        self.pr_comments = [review()]
        self.threads: list[dict[str, Any]] = []
        self.contents = {
            (CONFIG_PATH, DEFAULT_SHA): free_config(),
            (CONFIG_PATH, HEAD_SHA): free_config(),
        }
        self.external_runs = [external_run()]
        self.other: list[dict[str, Any]] = []
        self.ready_calls: list[str] = []
        self.merge_calls: list[tuple[int, str]] = []
        self.check_reads = 0
        self.check_race = None

    def repository(self): return copy.deepcopy(self.repo)
    def default_branch_sha(self, branch): return self.default_sha
    def pull(self, number):
        if number == 5: return copy.deepcopy(self.pr)
        return copy.deepcopy(next(item for item in self.other if item["number"] == number))
    def issue(self, number): return copy.deepcopy(self.source)
    def issue_comments(self, number):
        return copy.deepcopy(self.source_comments if number == 7 else self.pr_comments)
    def pull_files(self, number): return copy.deepcopy(self.files[number])
    def open_pulls(self): return [copy.deepcopy(self.pr), *copy.deepcopy(self.other)]
    def review_threads(self, number): return copy.deepcopy(self.threads)
    def file_content(self, path, ref): return self.contents[(path, ref)]
    def check_runs(self, head_sha):
        self.check_reads += 1
        if self.check_reads == 2 and self.check_race:
            self.check_race(self)
        return copy.deepcopy(self.external_runs)
    def mark_ready(self, node_id):
        self.ready_calls.append(node_id)
        self.pr["draft"] = False
        self.check_reads = 0
    def merge(self, number, head_sha): self.merge_calls.append((number, head_sha))


class FreeOnlyCoordinatorTest(unittest.TestCase):
    def test_exact_external_success_is_merge_eligible(self):
        self.assertEqual(evaluate_free_only(FakeClient(), REPO, 5).action, "merge")

    def test_draft_is_readied_then_expected_head_merged(self):
        client = FakeClient(draft=True)
        decision = supervise_free_only(client, REPO, 5)
        self.assertEqual(decision.head_sha, HEAD_SHA)
        self.assertEqual(client.ready_calls, ["PR_node_5"])
        self.assertEqual(client.merge_calls, [(5, HEAD_SHA)])

    def test_wrong_external_identity_or_state_is_blocked(self):
        mutations = [
            external_run(sha="c" * 40),
            external_run(pr=6),
            external_run(slug="github-actions", app_id=15368),
            external_run(app_id=9999),
            external_run(name="Workers Builds: other"),
            external_run(status="in_progress", conclusion=None),
            external_run(conclusion="failure"),
        ]
        for candidate in mutations:
            client = FakeClient()
            client.external_runs = [candidate]
            with self.subTest(candidate=candidate):
                with self.assertRaisesRegex(SupervisorError, "external checks"):
                    evaluate_free_only(client, REPO, 5)

    def test_candidate_cannot_select_its_own_external_validator(self):
        client = FakeClient()
        client.contents[(CONFIG_PATH, HEAD_SHA)] = free_config("Workers Builds: attacker")
        client.external_runs = [external_run(name="Workers Builds: attacker")]
        with self.assertRaisesRegex(SupervisorError, "external checks"):
            evaluate_free_only(client, REPO, 5)

    def test_candidate_cannot_leave_free_only_profile(self):
        client = FakeClient()
        client.contents[(CONFIG_PATH, HEAD_SHA)] = json.dumps(
            {"schema_version": 2, "execution_profile": "github-actions", "checks": []}
        ).encode()
        with self.assertRaisesRegex(SupervisorError, "leave the free-only"):
            evaluate_free_only(client, REPO, 5)

    def test_default_must_already_opt_into_free_only(self):
        client = FakeClient()
        client.contents[(CONFIG_PATH, DEFAULT_SHA)] = json.dumps(
            {"schema_version": 1, "checks": []}
        ).encode()
        with self.assertRaisesRegex(SupervisorError, "has not opted"):
            evaluate_free_only(client, REPO, 5)

    def test_external_evidence_race_fails_closed(self):
        client = FakeClient()
        client.check_race = lambda value: value.external_runs.append(external_run(101))
        with self.assertRaisesRegex(SupervisorError, "evidence changed"):
            evaluate_free_only(client, REPO, 5)

    def test_issue_scope_review_collision_and_hold_remain_required(self):
        client = FakeClient()
        client.files[5] = [{"filename": "outside.py"}]
        with self.assertRaisesRegex(SupervisorError, "exceed"):
            evaluate_free_only(client, REPO, 5)

        client = FakeClient()
        client.pr_comments = []
        with self.assertRaisesRegex(SupervisorError, "review"):
            evaluate_free_only(client, REPO, 5)

        client = FakeClient()
        client.other = [{"number": 6, "head": {"repo": {"full_name": REPO}}}]
        client.files[6] = [{"filename": "scripts/app.py"}]
        with self.assertRaisesRegex(SupervisorError, "overlaps"):
            evaluate_free_only(client, REPO, 5)

        client = FakeClient()
        client.pr["labels"] = [{"name": "ai-no-merge"}]
        with self.assertRaisesRegex(SupervisorError, "ai-no-merge"):
            evaluate_free_only(client, REPO, 5)


if __name__ == "__main__":
    unittest.main()

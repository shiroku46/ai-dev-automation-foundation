"""Focused tests for the GitHub-only coordinator supervisor."""
from __future__ import annotations

import copy
import unittest
from typing import Any, Sequence

from scripts.github_coordinator_supervisor import (
    SupervisorError,
    evaluate,
    parse_task_scope,
    review_evidence,
    supervise,
)

REPO = "owner/foundation"
DEFAULT_SHA = "a" * 40
HEAD_SHA = "b" * 40


def issue_body(paths: Sequence[str], *, protected: bool = False) -> str:
    lines = ["## Exact allowed paths", *[f"- `{path}`" for path in paths]]
    if protected:
        lines += [
            "", "<!-- foundation-protected-authorization", "category: protected test",
            "paths:", *[f"- {path}" for path in paths],
            "operation: change exactly the declared paths",
            "prohibited: no secret, deployment, settings, or destructive effects",
            "validation: CI, Unit Tests, and two coordinator review passes",
            "rollback: revert the merge commit", "-->",
        ]
    return "\n".join(lines)


def comment(comment_id: int, body: str, login: str = "owner") -> dict[str, Any]:
    timestamp = f"2026-08-05T00:00:{comment_id % 60:02d}Z"
    return {"id": comment_id, "body": body, "created_at": timestamp, "updated_at": timestamp, "user": {"login": login}}


def standard_review() -> dict[str, Any]:
    return comment(50, f"<!-- foundation-coordinator-review:{HEAD_SHA}:clean -->\nReviewed exact-head scope, implementation, tests, and merge boundary.")


def protected_reviews() -> list[dict[str, Any]]:
    return [
        comment(51, f"<!-- foundation-coordinator-review:{HEAD_SHA}:scope-security:clean -->\nVerified authorization, trust boundaries, permissions, and prohibited effects."),
        comment(52, f"<!-- foundation-coordinator-review:{HEAD_SHA}:correctness-race:clean -->\nVerified checks, evidence stability, hold state, and expected-head merge."),
    ]


class FakeClient:
    def __init__(self, protected: bool = False, draft: bool = False):
        path = ".github/workflows/supervisor.yml" if protected else "scripts/app.py"
        self.repo = {"full_name": REPO, "default_branch": "main", "owner": {"login": "owner"}}
        self.default_sha = DEFAULT_SHA
        self.pr = {
            "number": 5, "state": "open", "draft": draft, "mergeable": True,
            "node_id": "PR_node_5", "body": "Closes #7.\n", "labels": [],
            "head": {"sha": HEAD_SHA, "ref": "feature", "repo": {"full_name": REPO}},
            "base": {"ref": "main", "repo": {"full_name": REPO}},
        }
        self.source = {"number": 7, "state": "open", "body": issue_body([path], protected=protected), "updated_at": "2026-08-05T00:00:00Z", "user": {"login": "owner"}}
        self.files = {5: [{"filename": path}]}
        self.source_comments = []
        self.pr_comments = protected_reviews() if protected else [standard_review()]
        self.threads = []
        self.runs = [
            {"id": 101, "name": "CI", "event": "pull_request", "status": "completed", "conclusion": "success", "head_sha": HEAD_SHA, "updated_at": "2026-08-05T00:10:00Z", "repository": {"full_name": REPO}, "pull_requests": [{"number": 5}]},
            {"id": 102, "name": "Unit Tests", "event": "pull_request", "status": "completed", "conclusion": "success", "head_sha": HEAD_SHA, "updated_at": "2026-08-05T00:11:00Z", "repository": {"full_name": REPO}, "pull_requests": [{"number": 5}]},
        ]
        self.blobs = {
            (".github/workflows/ci.yml", HEAD_SHA): "c" * 40,
            (".github/workflows/ci.yml", DEFAULT_SHA): "c" * 40,
            (".github/workflows/unit-tests.yml", HEAD_SHA): "d" * 40,
            (".github/workflows/unit-tests.yml", DEFAULT_SHA): "d" * 40,
        }
        self.other = []
        self.ready_calls = []; self.merge_calls = []
        self.issue_reads = self.pr_reads = self.run_reads = self.thread_reads = 0
        self.issue_race = self.pr_race = self.run_race = self.thread_race = None

    def repository(self): return copy.deepcopy(self.repo)
    def default_branch_sha(self, branch): return self.default_sha
    def pull(self, number):
        self.pr_reads += 1
        if self.pr_reads == 2 and self.pr_race: self.pr_race(self)
        if number == 5: return copy.deepcopy(self.pr)
        return copy.deepcopy(next(item for item in self.other if item["number"] == number))
    def issue(self, number):
        self.issue_reads += 1
        if self.issue_reads == 2 and self.issue_race: self.issue_race(self)
        return copy.deepcopy(self.source)
    def issue_comments(self, number): return copy.deepcopy(self.source_comments if number == 7 else self.pr_comments)
    def pull_files(self, number): return copy.deepcopy(self.files[number])
    def open_pulls(self): return [copy.deepcopy(self.pr), *copy.deepcopy(self.other)]
    def workflow_runs(self, head):
        self.run_reads += 1
        if self.run_reads == 2 and self.run_race: self.run_race(self)
        return copy.deepcopy(self.runs)
    def review_threads(self, number):
        self.thread_reads += 1
        if self.thread_reads == 2 and self.thread_race: self.thread_race(self)
        return copy.deepcopy(self.threads)
    def file_blob(self, path, ref): return self.blobs[(path, ref)]
    def mark_ready(self, node_id):
        self.ready_calls.append(node_id); self.pr["draft"] = False
        self.issue_reads = self.pr_reads = self.run_reads = self.thread_reads = 0
    def merge(self, number, head): self.merge_calls.append((number, head))


class SupervisorTest(unittest.TestCase):
    def test_standard_and_protected_are_merge_eligible(self):
        self.assertEqual(evaluate(FakeClient(), REPO, 5).action, "merge")
        self.assertEqual(evaluate(FakeClient(protected=True), REPO, 5).risk, "protected")

    def test_protected_amendment_is_trusted_only_when_immutable(self):
        amendment = comment(9, "<!-- foundation-protected-authorization-amendment\ncategory: workflow\npaths:\n- .github/workflows/supervisor.yml\noperation: update\nprohibited: no secret access\nvalidation: tests\nrollback: revert\n-->")
        scope = parse_task_scope(issue_body(["scripts/app.py"]), [amendment], ["owner"])
        self.assertIn(".github/workflows/supervisor.yml", scope.paths)
        edited = dict(amendment, updated_at="2026-08-05T01:00:00Z")
        edited_scope = parse_task_scope(issue_body(["scripts/app.py"]), [edited], ["owner"])
        self.assertNotIn(".github/workflows/supervisor.yml", edited_scope.paths)

    def test_review_contract_and_unresolved_threads(self):
        self.assertEqual(review_evidence([standard_review()], [], HEAD_SHA, "standard", ["owner"]).state, "clean")
        self.assertEqual(review_evidence(protected_reviews(), [], HEAD_SHA, "protected", ["owner"]).state, "clean")
        self.assertEqual(review_evidence(protected_reviews(), [{"id": "t", "isResolved": False}], HEAD_SHA, "protected", ["owner"]).state, "pending")

    def test_hold_collision_and_candidate_workflow_block(self):
        client = FakeClient(); client.pr["labels"] = [{"name": "ai-no-merge"}]
        with self.assertRaisesRegex(SupervisorError, "ai-no-merge"): evaluate(client, REPO, 5)
        client = FakeClient(); client.other = [{"number": 6, "head": {"repo": {"full_name": REPO}}}]; client.files[6] = [{"filename": "scripts/app.py"}]
        with self.assertRaisesRegex(SupervisorError, "overlaps"): evaluate(client, REPO, 5)
        client = FakeClient(); client.blobs[(".github/workflows/ci.yml", HEAD_SHA)] = "e" * 40
        with self.assertRaisesRegex(SupervisorError, "workflow differs"): evaluate(client, REPO, 5)

    def test_mutable_evidence_races_fail_closed(self):
        client = FakeClient(); client.issue_race = lambda value: value.source.update(body=value.source["body"] + "x")
        with self.assertRaisesRegex(SupervisorError, "source Issue changed"): evaluate(client, REPO, 5)
        client = FakeClient(); client.run_race = lambda value: value.runs[0].update(conclusion="failure")
        with self.assertRaisesRegex(SupervisorError, "check evidence changed"): evaluate(client, REPO, 5)
        client = FakeClient(); client.thread_race = lambda value: value.threads.append({"id": "late", "isResolved": False})
        with self.assertRaisesRegex(SupervisorError, "review evidence changed"): evaluate(client, REPO, 5)
        client = FakeClient(); client.pr_race = lambda value: value.pr["head"].update(sha="f" * 40)
        with self.assertRaisesRegex(SupervisorError, "identity, body, head"): evaluate(client, REPO, 5)

    def test_draft_is_ready_then_freshly_merged(self):
        client = FakeClient(draft=True)
        supervise(client, REPO, 5)
        self.assertEqual(client.ready_calls, ["PR_node_5"])
        self.assertEqual(client.merge_calls, [(5, HEAD_SHA)])

    def test_provider_fields_are_not_gates(self):
        client = FakeClient(); client.pr["body"] += "selected_auditor: none\naudit_state: route-unavailable\n"
        self.assertEqual(evaluate(client, REPO, 5).action, "merge")


if __name__ == "__main__": unittest.main()

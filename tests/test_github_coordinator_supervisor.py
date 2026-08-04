"""Focused tests for the GitHub-only coordinator supervisor."""
from __future__ import annotations

import copy
import unittest
from typing import Any, Mapping, Sequence

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
        lines.extend(
            [
                "",
                "<!-- foundation-protected-authorization",
                "category: test protected change",
                "paths:",
                *[f"- {path}" for path in paths],
                "operation: change exactly the declared paths",
                "prohibited: no secrets, deployment, settings or destructive effects",
                "validation: CI, Unit Tests and two coordinator review passes",
                "rollback: revert the merge commit",
                "-->",
            ]
        )
    return "\n".join(lines)


def immutable_comment(comment_id: int, body: str, login: str = "owner") -> dict[str, Any]:
    timestamp = f"2026-08-05T00:00:{comment_id % 60:02d}Z"
    return {
        "id": comment_id,
        "body": body,
        "created_at": timestamp,
        "updated_at": timestamp,
        "user": {"login": login},
    }


def general_review(comment_id: int = 50, result: str = "clean") -> dict[str, Any]:
    return immutable_comment(
        comment_id,
        f"<!-- foundation-coordinator-review:{HEAD_SHA}:{result} -->\n"
        "Reviewed complete exact-head scope, implementation, tests, and merge boundary.",
    )


def protected_reviews() -> list[dict[str, Any]]:
    return [
        immutable_comment(
            51,
            f"<!-- foundation-coordinator-review:{HEAD_SHA}:scope-security:clean -->\n"
            "Scope and security pass verified authorization, trust boundaries, permissions, and prohibited effects.",
        ),
        immutable_comment(
            52,
            f"<!-- foundation-coordinator-review:{HEAD_SHA}:correctness-race:clean -->\n"
            "Correctness and race pass verified checks, evidence stability, hold state, and expected-head merge.",
        ),
    ]


class FakeClient:
    def __init__(self, *, protected: bool = False, draft: bool = False) -> None:
        path = ".github/workflows/supervisor.yml" if protected else "scripts/app.py"
        self.repo = {"full_name": REPO, "default_branch": "main", "owner": {"login": "owner"}}
        self.default_sha = DEFAULT_SHA
        self.pr_number = 5
        self.source_number = 7
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
        self.issue_data = {
            "number": 7,
            "state": "open",
            "body": issue_body([path], protected=protected),
            "updated_at": "2026-08-05T00:00:00Z",
            "user": {"login": "owner"},
        }
        self.files = {5: [{"filename": path, "status": "modified"}]}
        self.source_comments: list[dict[str, Any]] = []
        self.pr_comments = protected_reviews() if protected else [general_review()]
        self.threads: list[dict[str, Any]] = []
        self.runs = [
            {
                "id": 101, "name": "CI", "event": "pull_request", "status": "completed",
                "conclusion": "success", "head_sha": HEAD_SHA, "updated_at": "2026-08-05T00:10:00Z",
                "repository": {"full_name": REPO}, "pull_requests": [{"number": 5}],
            },
            {
                "id": 102, "name": "Unit Tests", "event": "pull_request", "status": "completed",
                "conclusion": "success", "head_sha": HEAD_SHA, "updated_at": "2026-08-05T00:11:00Z",
                "repository": {"full_name": REPO}, "pull_requests": [{"number": 5}],
            },
        ]
        self.blobs = {
            (".github/workflows/ci.yml", HEAD_SHA): "c" * 40,
            (".github/workflows/ci.yml", DEFAULT_SHA): "c" * 40,
            (".github/workflows/unit-tests.yml", HEAD_SHA): "d" * 40,
            (".github/workflows/unit-tests.yml", DEFAULT_SHA): "d" * 40,
        }
        self.other_pulls: list[dict[str, Any]] = []
        self.ready_calls: list[str] = []
        self.merge_calls: list[tuple[int, str]] = []
        self.issue_reads = self.pr_reads = self.run_reads = self.thread_reads = 0
        self.on_second_issue_read = self.on_second_pr_read = None
        self.on_second_run_read = self.on_second_thread_read = None

    def repository(self): return copy.deepcopy(self.repo)
    def default_branch_sha(self, branch): return self.default_sha
    def pull(self, number):
        self.pr_reads += 1
        if self.pr_reads == 2 and self.on_second_pr_read: self.on_second_pr_read(self)
        if number == 5: return copy.deepcopy(self.pr)
        for pull in self.other_pulls:
            if pull["number"] == number: return copy.deepcopy(pull)
        raise AssertionError(number)
    def issue(self, number):
        self.issue_reads += 1
        if self.issue_reads == 2 and self.on_second_issue_read: self.on_second_issue_read(self)
        return copy.deepcopy(self.issue_data)
    def issue_comments(self, number):
        return copy.deepcopy(self.source_comments if number == 7 else self.pr_comments)
    def pull_files(self, number): return copy.deepcopy(self.files[number])
    def open_pulls(self): return [copy.deepcopy(self.pr), *copy.deepcopy(self.other_pulls)]
    def workflow_runs(self, head_sha):
        self.run_reads += 1
        if self.run_reads == 2 and self.on_second_run_read: self.on_second_run_read(self)
        return copy.deepcopy(self.runs)
    def review_threads(self, number):
        self.thread_reads += 1
        if self.thread_reads == 2 and self.on_second_thread_read: self.on_second_thread_read(self)
        return copy.deepcopy(self.threads)
    def file_blob(self, path, ref): return self.blobs[(path, ref)]
    def mark_ready(self, node_id):
        self.ready_calls.append(node_id); self.pr["draft"] = False
        self.issue_reads = self.pr_reads = self.run_reads = self.thread_reads = 0
    def merge(self, number, head_sha): self.merge_calls.append((number, head_sha))


class ScopeTest(unittest.TestCase):
    def test_immutable_protected_amendment_adds_paths(self):
        amendment = immutable_comment(
            9,
            "<!-- foundation-protected-authorization-amendment\ncategory: workflow\npaths:\n"
            "- .github/workflows/supervisor.yml\noperation: update the supervisor\n"
            "prohibited: no secret access\nvalidation: tests\nrollback: revert\n-->",
        )
        scope = parse_task_scope(issue_body(["scripts/app.py"]), [amendment], ["owner"])
        self.assertEqual(scope.risk, "protected")
        self.assertIn(".github/workflows/supervisor.yml", scope.paths)

    def test_edited_or_untrusted_amendment_is_ignored(self):
        amendment = immutable_comment(
            10,
            "<!-- foundation-protected-authorization-amendment\ncategory: workflow\npaths:\n"
            "- .github/workflows/supervisor.yml\noperation: update\nprohibited: none\n"
            "validation: tests\nrollback: revert\n-->",
        )
        edited = dict(amendment, updated_at="2026-08-05T01:00:00Z")
        with self.assertRaises(SupervisorError):
            parse_task_scope(issue_body(["scripts/app.py"]), [edited], ["owner"])


class ReviewTest(unittest.TestCase):
    def test_standard_and_protected_review_contracts(self):
        self.assertEqual(review_evidence([general_review()], [], HEAD_SHA, "standard", ["owner"]).state, "clean")
        self.assertEqual(review_evidence(protected_reviews(), [], HEAD_SHA, "protected", ["owner"]).state, "clean")
        unresolved = review_evidence(protected_reviews(), [{"id": "t", "isResolved": False}], HEAD_SHA, "protected", ["owner"])
        self.assertEqual(unresolved.state, "pending")


class EvaluateTest(unittest.TestCase):
    def test_standard_and_protected_candidates(self):
        self.assertEqual(evaluate(FakeClient(), REPO, 5).action, "merge")
        self.assertEqual(evaluate(FakeClient(protected=True), REPO, 5).risk, "protected")

    def test_hold_collision_and_candidate_workflow_block(self):
        client = FakeClient(); client.pr["labels"] = [{"name": "ai-no-merge"}]
        with self.assertRaisesRegex(SupervisorError, "ai-no-merge"): evaluate(client, REPO, 5)
        client = FakeClient(); client.other_pulls = [{"number": 6, "head": {"repo": {"full_name": REPO}}}]
        client.files[6] = [{"filename": "scripts/app.py"}]
        with self.assertRaisesRegex(SupervisorError, "overlaps"): evaluate(client, REPO, 5)
        client = FakeClient(); client.blobs[(".github/workflows/ci.yml", HEAD_SHA)] = "e" * 40
        with self.assertRaisesRegex(SupervisorError, "workflow differs"): evaluate(client, REPO, 5)

    def test_mutable_evidence_races_fail_closed(self):
        client = FakeClient(); client.on_second_issue_read = lambda f: f.issue_data.update(body=f.issue_data["body"] + "x")
        with self.assertRaisesRegex(SupervisorError, "source Issue changed"): evaluate(client, REPO, 5)
        client = FakeClient(); client.on_second_run_read = lambda f: f.runs[0].update(conclusion="failure")
        with self.assertRaisesRegex(SupervisorError, "check evidence changed"): evaluate(client, REPO, 5)
        client = FakeClient(); client.on_second_thread_read = lambda f: f.threads.append({"id": "late", "isResolved": False})
        with self.assertRaisesRegex(SupervisorError, "review evidence changed"): evaluate(client, REPO, 5)
        client = FakeClient(); client.on_second_pr_read = lambda f: f.pr["head"].update(sha="f" * 40)
        with self.assertRaisesRegex(SupervisorError, "identity, body, head"): evaluate(client, REPO, 5)

    def test_draft_is_ready_then_freshly_merged(self):
        client = FakeClient(draft=True)
        decision = supervise(client, REPO, 5)
        self.assertEqual(decision.action, "merge")
        self.assertEqual(client.ready_calls, ["PR_node_5"])
        self.assertEqual(client.merge_calls, [(5, HEAD_SHA)])

    def test_provider_fields_are_ignored(self):
        client = FakeClient(); client.pr["body"] += "selected_auditor: none\naudit_state: route-unavailable\n"
        self.assertEqual(evaluate(client, REPO, 5).action, "merge")


if __name__ == "__main__":
    unittest.main()

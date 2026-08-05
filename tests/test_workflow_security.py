"""Repository-wide workflow and runtime security invariants."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN = re.compile(r"uses:\s*[^@\s]+@[0-9a-f]{40}\s*$")


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def job_block(content: str, name: str, following: str | None = None) -> str:
    block = content.split(f"\n  {name}:\n", 1)[1]
    if following:
        block = block.split(f"\n  {following}:\n", 1)[0]
    return block


class WorkflowSecurityTest(unittest.TestCase):
    def test_all_actions_are_immutably_pinned(self):
        for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
            content = path.read_text(encoding="utf-8")
            self.assertNotIn("pull_request_target", content)
            self.assertNotIn("\t", content)
            for line in content.splitlines():
                if line.strip().removeprefix("- ").startswith("uses:"):
                    self.assertRegex(line, PIN, path)

    def test_candidate_ci_and_unit_jobs_are_read_only(self):
        for name in ("ci.yml", "unit-tests.yml"):
            content = read(f".github/workflows/{name}")
            self.assertIn("pull_request:", content)
            self.assertIn("permissions:\n  contents: read", content)
            self.assertIn("persist-credentials: false", content)
            for forbidden in (
                "secrets.", "id-token: write", "contents: write",
                "actions: write", "checks: write", "statuses: write",
            ):
                self.assertNotIn(forbidden, content)

    def test_optional_queue_is_explicit_and_default_branch_controlled(self):
        queue = read(".github/workflows/claude-queue.yml")
        self.assertIn("issue_comment:\n    types: [created]", queue)
        self.assertIn("workflow_dispatch:", queue)
        self.assertNotIn("\n  issues:\n", queue)
        self.assertNotIn("workflow_run:", queue)
        self.assertNotIn("schedule:", queue)
        self.assertIn('trigger = "/claude-run"', queue)
        self.assertIn("body.strip() == trigger", queue)
        self.assertIn("ACTOR: ${{ github.actor }}", queue)
        self.assertIn("OWNER: ${{ vars.AUTOMATION_OWNER || github.repository_owner }}", queue)
        self.assertNotIn("github.triggering_actor", queue)

    def test_optional_provider_credentials_never_share_write_permission(self):
        queue = read(".github/workflows/claude-queue.yml")
        implement = job_block(queue, "implement", "verify")
        for required in (
            "contents: read", "issues: read", "pull-requests: read",
            "id-token: write", "persist-credentials: false", "track_progress: false",
        ):
            self.assertIn(required, implement)
        for forbidden in ("contents: write", "issues: write", "pull-requests: write"):
            self.assertNotIn(forbidden, implement)

        verify = job_block(queue, "verify", "publish")
        for forbidden in ("secrets.", "id-token: write", "contents: write", "pull-requests: write"):
            self.assertNotIn(forbidden, verify)

        handoff = job_block(queue, "publish", "finalize")
        self.assertIn("permissions:\n      contents: read", handoff)
        self.assertIn("publication_route: GitHub-direct coordinator", handoff)
        self.assertIn("repository_write: false", handoff)
        for forbidden in ("secrets.", "id-token: write", "contents: write", "pull-requests: write"):
            self.assertNotIn(forbidden, handoff)

    def test_permission_preflight_and_checkpoint_are_non_notifying(self):
        queue = read(".github/workflows/claude-queue.yml")
        self.assertIn("check_tool_permission_contract", queue)
        self.assertIn("foundation-provider-required-commands", queue)
        self.assertIn("contract_ok", queue)
        self.assertIn("continue-on-error: true", queue)
        self.assertIn("reserve the final 5 turns", queue)
        self.assertIn('"complete" if', queue)
        self.assertIn('else "wip"', queue)
        self.assertIn("retry_identity", queue)
        self.assertIn("changed_paths", queue)
        self.assertIn("notification: false", queue)
        self.assertIn("human_action_required: false", queue)
        self.assertNotIn("gh issue comment", job_block(queue, "finalize"))

    def test_reconciliation_splits_read_only_observation_from_bounded_recovery(self):
        reconcile = read(".github/workflows/ci-reconcile.yml")
        self.assertIn('workflows: ["CI", "Unit Tests", "Claude Issue Queue"]', reconcile)
        self.assertIn("schedule:", reconcile)
        self.assertIn("cancel-in-progress: false", reconcile)

        observe = job_block(reconcile, "observe", "queue_recovery")
        self.assertIn("actions: read", observe)
        self.assertIn("contents: read", observe)
        self.assertIn("pull-requests: read", observe)
        self.assertIn("read-only compatibility observation", observe)
        self.assertIn("provider_invocation: false", observe)
        self.assertIn("human_action_required: false", observe)
        for forbidden in (
            "actions: write", "contents: write", "issues: write",
            "pull-requests: write",
        ):
            self.assertNotIn(forbidden, observe)

        recovery = job_block(reconcile, "queue_recovery")
        for required in (
            "actions: write", "contents: write", "issues: read",
            "pull-requests: write", "max_retries = 3", "should_auto_retry",
            "candidate_execution_with_write_token: `false`",
            "notification: `false`", "human_action_required: `false`",
        ):
            self.assertIn(required, recovery)
        for forbidden in (
            "secrets.", "id-token: write", "issues: write", "anthropics/",
            "codex", "gh issue comment",
        ):
            self.assertNotIn(forbidden, recovery)

    def test_supervisor_is_default_branch_github_coordinator_only(self):
        supervisor = read(".github/workflows/supervisor.yml")
        self.assertIn('workflows: ["CI", "Unit Tests"]', supervisor)
        self.assertIn("issue_comment:", supervisor)
        self.assertIn("schedule:", supervisor)
        self.assertIn("workflow_dispatch:", supervisor)
        self.assertIn("ref: ${{ github.event.repository.default_branch }}", supervisor)
        self.assertIn("persist-credentials: false", supervisor)
        self.assertIn("actions: read", supervisor)
        self.assertIn("contents: write", supervisor)
        self.assertIn("issues: read", supervisor)
        self.assertIn("pull-requests: write", supervisor)
        self.assertIn("python -m scripts.github_coordinator_supervisor", supervisor)
        for forbidden in (
            "actions: write", "issues: write", "secrets.", "id-token: write",
            "Claude Issue Queue", "supervisor_final_guard", "supervisor_queue_recovery",
            "anthropics/", "codex",
        ):
            self.assertNotIn(forbidden, supervisor)

    def test_github_coordinator_runtime_fails_closed(self):
        runtime = read("scripts/github_coordinator_supervisor.py")
        for required in (
            "foundation-coordinator-review", "foundation-protected-authorization",
            "foundation-protected-authorization-amendment", "previous_filename",
            "ai-no-merge", "workflow differs from the default-branch definition",
            "exact-head check evidence changed during evaluation",
            "coordinator review evidence changed during evaluation",
            "expected-head merge was rejected", "human_action_required",
        ):
            self.assertIn(required, runtime)
        self.assertNotIn("secrets.", runtime)
        self.assertNotIn("actions/checkout", runtime)

    def test_legacy_internal_stops_remain_non_commenting(self):
        runtime = read("scripts/supervisor_runtime.py")
        self.assertIn('INTERNAL_STOP_BRANCH = "automation-internal-stops"', runtime)
        self.assertIn('INTERNAL_STOP_ROOT = "automation-stops"', runtime)
        self.assertIn("persist_internal_stop_record", runtime)
        stop = runtime.split("def stop_report(", 1)[1].split("\ndef format_human_only_notice(", 1)[0]
        self.assertNotIn("comment(", stop)
        self.assertNotIn("/comments", stop)
        self.assertNotIn("gh issue comment", stop)

    def test_human_only_notice_still_requires_connected_evidence(self):
        runtime = read("scripts/supervisor_runtime.py")
        for reason in (
            "HUMAN_ONLY_ACCOUNT_LEVEL_REPOSITORY_CREATION_UI_UNAVAILABLE",
            "HUMAN_ONLY_CREDENTIAL_PROVIDER_UI_REQUIRED",
            "HUMAN_ONLY_DISCONNECTED_INTEGRATION_RECONNECTION_UI_REQUIRED",
        ):
            self.assertIn(reason, runtime)
        notice = runtime.split("def human_only_notice(", 1)[1].split("\ndef discover_targets(", 1)[0]
        self.assertIn("self_resolution_audit", notice)
        self.assertIn('item.get("created_at") == item.get("updated_at")', notice)

    def test_guidance_and_bootstrap_keep_internal_stop_parity(self):
        for path in ("docs/OPERATING_RULES.md", "AGENTS.md", "CLAUDE.md", "bootstrap/generator.py"):
            self.assertIn("automation-internal-stops", read(path))
        generator = read("bootstrap/generator.py")
        self.assertIn("MANAGED_FILES", generator)
        self.assertIn("ALLOWLIST = MANAGED_FILES", generator)
        self.assertIn("write_bytes(source.read_bytes())", generator)


if __name__ == "__main__":
    unittest.main()

"""Security regressions for the explicitly optional Claude Queue."""
from __future__ import annotations

import re
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT = (ROOT / ".github/workflows/claude-queue.yml").read_text(encoding="utf-8")
PIN = re.compile(r"uses:\s*[^@\s]+@[0-9a-f]{40}\s*$")
HEREDOC = re.compile(
    r"(?ms)^          python3 - <<'PY'\n(?P<body>.*?)^          PY$"
)


def block(name: str, following: str | None = None) -> str:
    value = TEXT.split(f"\n  {name}:\n", 1)[1]
    if following:
        value = value.split(f"\n  {following}:\n", 1)[0]
    return value


def embedded_python() -> list[str]:
    return [textwrap.dedent(match.group("body")) for match in HEREDOC.finditer(TEXT)]


class OptionalQueueTest(unittest.TestCase):
    def test_only_explicit_events_select_provider(self):
        self.assertIn("issue_comment:\n    types: [created]", TEXT)
        self.assertIn("workflow_dispatch:", TEXT)
        self.assertNotIn("\n  issues:\n", TEXT)
        self.assertNotIn("workflow_run:", TEXT)
        self.assertNotIn("schedule:", TEXT)
        self.assertIn('trigger = "/claude-run"', TEXT)
        self.assertIn("body.strip() == trigger", TEXT)
        prepare = block("prepare", "implement")
        self.assertIn("REF_NAME: ${{ github.ref_name }}", prepare)
        self.assertIn('os.environ.get("REF_NAME") == os.environ["DEFAULT_BRANCH"]', prepare)

    def test_actions_are_pinned(self):
        for line in TEXT.splitlines():
            if line.strip().removeprefix("- ").startswith("uses:"):
                self.assertRegex(line, PIN)

    def test_every_embedded_python_block_compiles(self):
        sources = embedded_python()
        self.assertEqual(len(sources), 2)
        for index, source in enumerate(sources, start=1):
            with self.subTest(index=index):
                compile(source, f"claude-queue-heredoc-{index}", "exec")
        self.assertIn("check_tool_permission_contract", sources[0])
        self.assertIn("checkpoint digest mismatch", sources[1])

    def test_run_names_bind_retry_identity_and_ignore_control_comments(self):
        for required in (
            "format('Optional Claude issue-{0} retry-{1} request-{2}'",
            "format('Optional Claude issue-{0} manual'",
            "format('Optional Claude issue-{0} trigger'",
            "format('Optional Claude ignored issue-{0}'",
        ):
            self.assertIn(required, TEXT)
        self.assertNotIn("run-name: Optional Claude issue-${{ inputs.issue_number", TEXT)

    def test_automated_retry_routes_to_github_direct_without_provider(self):
        implement = block("implement", "verify")
        for required in (
            "Optional provider missing secret", "credential-isolated route unavailable",
            "provider_invocation: false", "repository_credentials_exposed: false",
            "repository_write: false", "Continue through the authoritative GitHub-direct route",
            "human_action_required: false", "timeout-minutes: 5",
        ):
            self.assertIn(required, implement)
        for forbidden in (
            "--max-turns", "allowedTools", "Automated retry attempt:",
            "anthropics/", "claude_code_oauth_token", "secrets.",
        ):
            self.assertNotIn(forbidden, implement)

    def test_permission_preflight_is_before_provider(self):
        prepare = block("prepare", "implement")
        self.assertIn("check_tool_permission_contract", prepare)
        self.assertIn("foundation-provider-required-commands", prepare)
        self.assertIn("contract_ok", prepare)
        self.assertIn("model_invocation: `skipped`", prepare)
        self.assertIn("notification: false", prepare)
        self.assertIn("human_action_required: false", prepare)

    def test_provider_job_exposes_no_repository_or_oidc_credentials(self):
        implement = block("implement", "verify")
        self.assertIn("permissions: {}", implement)
        self.assertIn("exit 1", implement)
        for forbidden in (
            "id-token: write", "contents: read", "issues: read", "pull-requests: read",
            "contents: write", "issues: write", "pull-requests: write",
            "persist-credentials", "GH_TOKEN", "github.token", "remote.origin",
            "allowed_bots", "track_progress", "anthropics/", "secrets.",
        ):
            self.assertNotIn(forbidden, implement)

    def test_automated_retry_guard_remains_exact(self):
        prepare = block("prepare", "implement")
        for required in (
            'automation_actor = "github-actions[bot]"',
            'os.environ.get("RUN_ATTEMPT") == "1"',
            're.fullmatch(r"[0-9a-f]{20}", fingerprint_input)',
            'attempt_input in {"1", "2", "3"}',
            'record.get("base_sha") == base_sha',
            'record.get("issue_number") == number',
            'record.get("request_fingerprint") == fingerprint',
            'record.get("attempt") == attempt',
            'record.get("notification") is False',
            'record.get("human_action_required") is False',
            'should_auto_retry(failure_class, attempt - 1, 3)',
        ):
            self.assertIn(required, prepare)

    def test_unavailable_provider_creates_no_checkpoint_or_artifact(self):
        implement = block("implement", "verify")
        self.assertIn('checkpoint_kind: ${{ steps.unavailable.outputs.checkpoint_kind }}', implement)
        self.assertIn('artifact_sha256: ${{ steps.unavailable.outputs.artifact_sha256 }}', implement)
        self.assertIn('echo "checkpoint_kind="', implement)
        self.assertIn('echo "artifact_sha256="', implement)
        for forbidden in (
            "queue-checkpoint", "candidate.patch", "checkpoint.json",
            "upload-artifact@", "git add", "git diff", "git push",
        ):
            self.assertNotIn(forbidden, implement)

    def test_unavailable_provider_has_no_local_repository_mutation(self):
        implement = block("implement", "verify")
        for forbidden in (
            "actions/checkout@", "git ", "python3", "Write,Edit", "branch_prefix",
            "base_branch", "track_progress", "allowed_bots",
        ):
            self.assertNotIn(forbidden, implement)
        self.assertIn("exact_base_sha", implement)
        self.assertIn("repository_write: false", implement)

    def test_verification_has_no_secret_oidc_or_write(self):
        verify = block("verify", "publish")
        for required in (
            "contents: read", "persist-credentials: false", "git apply --index",
            "python scripts/public_export_guard.py .", "python scripts/validate_repository.py",
        ):
            self.assertIn(required, verify)
        for forbidden in ("secrets.", "id-token: write", "contents: write", "pull-requests: write"):
            self.assertNotIn(forbidden, verify)

    def test_handoff_has_no_repository_write(self):
        publish = block("publish", "finalize")
        self.assertIn("contents: read", publish)
        self.assertIn("publication_route: GitHub-direct coordinator", publish)
        self.assertIn("repository_write: false", publish)
        for forbidden in ("contents: write", "pull-requests: write", "secrets.", "id-token: write", "anthropics/"):
            self.assertNotIn(forbidden, publish)

    def test_final_state_is_non_notifying(self):
        finalize = block("finalize")
        self.assertIn("notification: false", finalize)
        self.assertIn("human_action_required: false", finalize)
        self.assertIn("Continue GitHub-direct work", finalize)
        self.assertNotIn("gh issue comment", finalize)


if __name__ == "__main__": unittest.main()

#!/usr/bin/env python3
"""Update validator and legacy integration tests for the provider-free Queue route."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def replace_method(text: str, name: str, replacement: str) -> str:
    pattern = re.compile(
        rf"(?ms)^    def {re.escape(name)}\(self\):\n.*?(?=^    def |\n\nif __name__)"
    )
    value = replacement.rstrip() + "\n\n"
    updated, count = pattern.subn(lambda _match: value, text, count=1)
    if count != 1:
        raise SystemExit(f"method {name}: expected one match, found {count}")
    return updated


validator_path = ROOT / "scripts/validate_repository.py"
validator = validator_path.read_text(encoding="utf-8")
queue_start = validator.index('    queue = text(".github/workflows/claude-queue.yml")')
require_start = validator.index("    require(queue, (", queue_start)
require_end_marker = '    ), "optional Queue")\n'
require_end = validator.index(require_end_marker, require_start) + len(require_end_marker)
new_require = '''    require(queue, (
        'trigger = "/claude-run"', "body.strip() == trigger",
        "check_tool_permission_contract", "contract_ok",
        "Optional provider missing secret", "credential-isolated route unavailable",
        "provider_invocation: false", "repository_credentials_exposed: false",
        "repository_write: false", "notification: false", "human_action_required: false",
        "exit 1", "publication_route: GitHub-direct coordinator", "request_fingerprint:",
        "retry_attempt:", 'automation_actor = "github-actions[bot]"',
        "automation-stops/queue-v4/issue-", "automation-internal-stops",
        'record.get("next_automatic_action") == "dispatch one optional Queue retry"',
        "should_auto_retry(failure_class, attempt - 1, 3)",
    ), "optional Queue")
'''
validator = validator[:require_start] + new_require + validator[require_end:]
implement_start = validator.index('    implement = job(queue, "implement", "verify")', queue_start)
verify_start = validator.index('    verify = job(queue, "verify", "publish")', implement_start)
new_implement = '''    implement = job(queue, "implement", "verify")
    require(implement, (
        "timeout-minutes: 5", "permissions: {}", "Optional provider missing secret",
        "credential-isolated route unavailable", "provider_invocation: false",
        "repository_credentials_exposed: false", "repository_write: false",
        "notification: false", "human_action_required: false", "exit 1",
    ), "credential-isolated optional route")
    for forbidden in (
        "continue-on-error: true", "secrets.", "id-token: write", "anthropics/",
        "actions/checkout@", "GH_TOKEN", "github.token", "persist-credentials",
        "remote.origin", "contents: write", "issues: write", "pull-requests: write",
        "queue-checkpoint", "candidate.patch", "checkpoint.json", "upload-artifact@",
    ):
        if forbidden in implement:
            raise ValidationError(f"credential-isolated optional route retains forbidden capability: {forbidden}")
'''
validator = validator[:implement_start] + new_implement + validator[verify_start:]
validator_path.write_text(validator, encoding="utf-8")

integration_path = ROOT / "tests/test_queue_and_final_guard.py"
integration = integration_path.read_text(encoding="utf-8")
integration = replace_method(
    integration,
    "test_provider_job_cannot_publish",
    r'''    def test_provider_job_is_explicitly_unavailable_and_credential_free(self):
        queue = workflow("claude-queue.yml")
        implement = job_block(queue, "implement", "verify")
        for required in (
            "timeout-minutes: 5", "permissions: {}", "Optional provider missing secret",
            "credential-isolated route unavailable", "provider_invocation: false",
            "repository_credentials_exposed: false", "repository_write: false",
            "notification: false", "human_action_required: false", "exit 1",
        ):
            self.assertIn(required, implement)
        for forbidden in (
            "continue-on-error: true", "contents: read", "issues: read", "pull-requests: read",
            "id-token: write", "persist-credentials", "track_progress", "allowed_bots",
            "anthropics/", "secrets.", "GH_TOKEN", "github.token", "actions/checkout@",
            "contents: write", "issues: write", "pull-requests: write",
        ):
            self.assertNotIn(forbidden, implement)''',
)
integration = replace_method(
    integration,
    "test_complete_and_wip_checkpoints_are_durable_and_bounded",
    r'''    def test_unavailable_provider_creates_no_checkpoint_or_artifact(self):
        queue = workflow("claude-queue.yml")
        implement = job_block(queue, "implement", "verify")
        self.assertIn('checkpoint_kind: ${{ steps.unavailable.outputs.checkpoint_kind }}', implement)
        self.assertIn('artifact_sha256: ${{ steps.unavailable.outputs.artifact_sha256 }}', implement)
        self.assertIn('echo "checkpoint_kind="', implement)
        self.assertIn('echo "artifact_sha256="', implement)
        for forbidden in (
            '"complete" if', 'else "wip"', "retry_identity", "changed_paths",
            "patch_sha256", "empty or unauthorized checkpoint",
            "checkpoint leaves must be regular files", "actions/upload-artifact@",
            "retention-days: 1", "queue-checkpoint", "candidate.patch", "checkpoint.json",
            "git add", "git diff", "git push",
        ):
            self.assertNotIn(forbidden, implement)''',
)
integration_path.write_text(integration, encoding="utf-8")

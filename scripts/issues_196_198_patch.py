#!/usr/bin/env python3
"""Generate the exact protected workflow/test patch for Issues #196 and #198."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = ROOT / "automation-tmp"
TMP.mkdir(exist_ok=True)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def replace_method(text: str, name: str, replacement: str) -> str:
    pattern = re.compile(
        rf"(?ms)^    def {re.escape(name)}\(self\):\n.*?(?=^    def |\n\nif __name__)"
    )
    updated, count = pattern.subn(replacement.rstrip() + "\n\n", text, count=1)
    if count != 1:
        raise SystemExit(f"method {name}: expected one match, found {count}")
    return updated


ci_path = ROOT / ".github/workflows/ci-reconcile.yml"
ci = ci_path.read_text(encoding="utf-8")
ci = replace_once(
    ci,
    "          from scripts.queue_retry_identity import (\n",
    "          from scripts.queue_issue_hydration import resolve_stable_issue\n"
    "          from scripts.queue_retry_identity import (\n",
    "queue hydration import",
)
start = ci.index("          def issue_trust_predicates(")
end = ci.index("          def trigger_identity(", start)
ci_block = '''          def fresh_issue(number: int) -> dict[str, Any]:
              completed = subprocess.run(
                  [
                      "gh", "api",
                      "-H", "Accept: application/vnd.github+json",
                      "-H", "Cache-Control: no-cache",
                      "-H", "Pragma: no-cache",
                      f"repos/{repository}/issues/{number}",
                  ],
                  text=True,
                  capture_output=True,
                  check=False,
              )
              if completed.returncode != 0:
                  raise RuntimeError("connected Issue read failed")
              try:
                  value = json.loads(completed.stdout)
              except json.JSONDecodeError as exc:
                  raise RuntimeError("connected Issue response was malformed") from exc
              if not isinstance(value, dict):
                  raise RuntimeError("connected Issue response was not an object")
              return value

          def trusted_issue(number: int) -> dict[str, Any]:
              if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
                  raise RuntimeError("Queue source Issue trust predicates failed: invalid_number=true")
              delays = (0.0, 0.5, 1.0, 2.0)
              samples: list[dict[str, Any]] = []
              last = {
                  "exact_number": False,
                  "open_state": False,
                  "issue_not_pr": False,
                  "trusted_author": False,
              }
              stability = "unstable"
              for delay in delays:
                  if delay:
                      time.sleep(delay)
                  try:
                      issue = fresh_issue(number)
                  except RuntimeError:
                      continue
                  samples.append(issue)
                  stability, trusted, last = resolve_stable_issue(
                      samples,
                      number=number,
                      allowed_owners=trusted_owners,
                      required_matches=2,
                  )
                  if stability == "trusted" and trusted is not None:
                      return trusted
              detail = ",".join(
                  f"{key}={str(value).lower()}" for key, value in sorted(last.items())
              )
              raise RuntimeError(
                  f"Queue source Issue trust predicates failed: stability={stability},{detail}"
              )

'''
ci = ci[:start] + ci_block + ci[end:]
(ROOT / "automation-tmp/issues-196-198-ci-reconcile.yml").write_text(ci, encoding="utf-8")

queue_path = ROOT / ".github/workflows/claude-queue.yml"
queue = queue_path.read_text(encoding="utf-8")
start = queue.index("\n  implement:\n")
end = queue.index("\n  verify:\n", start)
implement = '''
  implement:
    needs: prepare
    if: needs.prepare.outputs.should_run == 'true' && needs.prepare.outputs.contract_ok == 'true'
    runs-on: ubuntu-latest
    timeout-minutes: 5
    permissions: {}
    outputs:
      checkpoint_kind: ${{ steps.unavailable.outputs.checkpoint_kind }}
      artifact_sha256: ${{ steps.unavailable.outputs.artifact_sha256 }}
    steps:
      - id: unavailable
        name: Optional provider missing secret; credential-isolated route unavailable
        env:
          BASE_SHA: ${{ needs.prepare.outputs.base_sha }}
        run: |
          echo "checkpoint_kind=" >> "$GITHUB_OUTPUT"
          echo "artifact_sha256=" >> "$GITHUB_OUTPUT"
          {
            echo "## Optional provider route unavailable"
            echo
            echo "- exact_base_sha: \`$BASE_SHA\`"
            echo "- provider_invocation: false"
            echo "- repository_credentials_exposed: false"
            echo "- repository_write: false"
            echo "- notification: false"
            echo "- human_action_required: false"
            echo "- next_action: Continue through the authoritative GitHub-direct route."
          } >> "$GITHUB_STEP_SUMMARY"
          exit 1
'''
queue = queue[:start] + implement + queue[end:]
(ROOT / "automation-tmp/issues-196-198-claude-queue.yml").write_text(queue, encoding="utf-8")

security_path = ROOT / "tests/test_workflow_security.py"
security = security_path.read_text(encoding="utf-8")
security = replace_method(
    security,
    "test_optional_provider_credentials_never_share_write_permission",
    '''    def test_optional_provider_route_is_credential_isolated_and_nonblocking(self):
        queue = read(".github/workflows/claude-queue.yml")
        implement = job_block(queue, "implement", "verify")
        for required in (
            "permissions: {}", "Optional provider missing secret",
            "credential-isolated route unavailable", "provider_invocation: false",
            "repository_credentials_exposed: false", "repository_write: false",
            "human_action_required: false", "exit 1",
        ):
            self.assertIn(required, implement)
        for forbidden in (
            "secrets.", "id-token: write", "anthropics/", "actions/checkout@",
            "GH_TOKEN", "persist-credentials", "remote.origin", "contents: write",
            "issues: write", "pull-requests: write",
        ):
            self.assertNotIn(forbidden, implement)

        verify = job_block(queue, "verify", "publish")
        for forbidden in ("secrets.", "id-token: write", "contents: write", "pull-requests: write"):
            self.assertNotIn(forbidden, verify)

        handoff = job_block(queue, "publish", "finalize")
        self.assertIn("permissions:\n      contents: read", handoff)
        self.assertIn("publication_route: GitHub-direct coordinator", handoff)
        self.assertIn("repository_write: false", handoff)
        for forbidden in ("secrets.", "id-token: write", "contents: write", "pull-requests: write"):
            self.assertNotIn(forbidden, handoff)''',
)
security = replace_method(
    security,
    "test_permission_preflight_and_checkpoint_are_non_notifying",
    '''    def test_permission_preflight_and_unavailable_route_are_non_notifying(self):
        queue = read(".github/workflows/claude-queue.yml")
        prepare = job_block(queue, "prepare", "implement")
        implement = job_block(queue, "implement", "verify")
        self.assertIn("check_tool_permission_contract", prepare)
        self.assertIn("foundation-provider-required-commands", prepare)
        self.assertIn("contract_ok", prepare)
        self.assertIn("provider_invocation: false", implement)
        self.assertIn("notification: false", implement)
        self.assertIn("human_action_required: false", implement)
        self.assertNotIn("queue-checkpoint", implement)
        self.assertNotIn("upload-artifact", implement)
        self.assertNotIn("gh issue comment", job_block(queue, "finalize"))''',
)
security = replace_method(
    security,
    "test_reconciliation_control_and_trusted_issue_identity",
    '''    def test_reconciliation_control_and_trusted_issue_identity(self):
        reconcile = read(".github/workflows/ci-reconcile.yml")
        recovery = job_block(reconcile, "queue_recovery")
        for required in (
            "issue_comment:\n    types: [created]",
            "github.event.comment.body == '/foundation-reconcile'",
            "github.actor == github.repository_owner",
            "github.event.issue.pull_request == null",
            "REPOSITORY_OWNER: ${{ github.repository_owner }}",
            "ACTOR: ${{ github.actor }}",
            'os.environ["OWNER"].strip().casefold()',
            'os.environ["REPOSITORY_OWNER"].strip().casefold()',
            'os.environ["ACTOR"].strip().casefold()',
            'control_trigger = "/foundation-reconcile"',
            'elif event_name == "issue_comment"',
            'comment_author != event_actor',
            'source.get("pull_request")',
            'return trusted_issue(number)',
            "from scripts.queue_issue_hydration import resolve_stable_issue",
        ):
            self.assertIn(required, reconcile)
        self.assertNotIn("configured_owner=", recovery)
        self.assertNotIn("repository_owner=", recovery)

        identity = reconcile.split("          def fresh_issue(", 1)[1].split(
            "\n          def trigger_identity(", 1
        )[0]
        for required in (
            '"Cache-Control: no-cache"', '"Pragma: no-cache"',
            "delays = (0.0, 0.5, 1.0, 2.0)", "time.sleep(delay)",
            "samples.append(issue)", "resolve_stable_issue(",
            "required_matches=2", 'stability == "trusted"',
            "Queue source Issue trust predicates failed: stability=",
        ):
            self.assertIn(required, identity)
        self.assertEqual(identity.count("fresh_issue(number)"), 1)
        for forbidden in (
            'issue.get("body")', "completed.stderr", "authorization", "token=",
            "configured_owner=", "repository_owner=",
        ):
            self.assertNotIn(forbidden, identity.lower())''',
)
security_path.write_text(security, encoding="utf-8")

queue_test_path = ROOT / "tests/test_queue_workflow.py"
queue_test = queue_test_path.read_text(encoding="utf-8")
queue_test = replace_method(
    queue_test,
    "test_every_embedded_python_block_compiles",
    '''    def test_every_embedded_python_block_compiles(self):
        sources = embedded_python()
        self.assertEqual(len(sources), 2)
        for index, source in enumerate(sources, start=1):
            with self.subTest(index=index):
                compile(source, f"claude-queue-heredoc-{index}", "exec")
        self.assertIn("check_tool_permission_contract", sources[0])
        self.assertIn("checkpoint digest mismatch", sources[1])''',
)
queue_test = replace_method(
    queue_test,
    "test_automated_retry_prompt_is_edit_first_and_bounded",
    '''    def test_automated_retry_routes_to_github_direct_without_provider(self):
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
            self.assertNotIn(forbidden, implement)''',
)
queue_test = replace_method(
    queue_test,
    "test_provider_job_is_read_only_and_agent_mode",
    '''    def test_provider_job_exposes_no_repository_or_oidc_credentials(self):
        implement = block("implement", "verify")
        self.assertIn("permissions: {}", implement)
        self.assertIn("exit 1", implement)
        for forbidden in (
            "id-token: write", "contents: read", "issues: read", "pull-requests: read",
            "contents: write", "issues: write", "pull-requests: write",
            "persist-credentials", "GH_TOKEN", "github.token", "remote.origin",
            "allowed_bots", "track_progress", "anthropics/", "secrets.",
        ):
            self.assertNotIn(forbidden, implement)''',
)
queue_test = replace_method(
    queue_test,
    "test_complete_or_wip_checkpoint_is_bounded",
    '''    def test_unavailable_provider_creates_no_checkpoint_or_artifact(self):
        implement = block("implement", "verify")
        self.assertIn('checkpoint_kind: ${{ steps.unavailable.outputs.checkpoint_kind }}', implement)
        self.assertIn('artifact_sha256: ${{ steps.unavailable.outputs.artifact_sha256 }}', implement)
        self.assertIn('echo "checkpoint_kind="', implement)
        self.assertIn('echo "artifact_sha256="', implement)
        for forbidden in (
            "queue-checkpoint", "candidate.patch", "checkpoint.json",
            "upload-artifact@", "git add", "git diff", "git push",
        ):
            self.assertNotIn(forbidden, implement)''',
)
queue_test = replace_method(
    queue_test,
    "test_checkpoint_patch_includes_authorized_untracked_files",
    '''    def test_unavailable_provider_has_no_local_repository_mutation(self):
        implement = block("implement", "verify")
        for forbidden in (
            "actions/checkout@", "git ", "python3", "Write,Edit", "branch_prefix",
            "base_branch", "track_progress", "allowed_bots",
        ):
            self.assertNotIn(forbidden, implement)
        self.assertIn("exact_base_sha", implement)
        self.assertIn("repository_write: false", implement)''',
)
queue_test_path.write_text(queue_test, encoding="utf-8")

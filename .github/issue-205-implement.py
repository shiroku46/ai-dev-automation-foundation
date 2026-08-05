#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(content: str, old: str, new: str, path: str) -> str:
    if content.count(old) != 1:
        raise RuntimeError(f"{path}: expected one replacement, found {content.count(old)}")
    return content.replace(old, new, 1)


# Queue admission: use only bounded scalar GitHub context and a pure unit-tested guard.
path = ".github/workflows/claude-queue.yml"
queue = read(path)
queue = replace_once(
    queue,
    "          EVENT_PATH: ${{ github.event_path }}\n",
    "          COMMENT_ISSUE: ${{ github.event.issue.number || '' }}\n"
    "          COMMENT_BODY: ${{ github.event.comment.body || '' }}\n"
    "          COMMENT_IS_PR: ${{ github.event.issue.pull_request && 'true' || 'false' }}\n",
    path,
)
queue = replace_once(queue, "          from pathlib import Path\n", "", path)
queue = replace_once(
    queue,
    "          from scripts.queue_failure_classifier import (\n",
    "          from scripts.queue_event_guard import resolve_queue_event\n"
    "          from scripts.queue_failure_classifier import (\n",
    path,
)
start_marker = '          event = json.loads(Path(os.environ["EVENT_PATH"]).read_text(encoding="utf-8"))\n'
end_marker = "          if not isinstance(number, int) or number <= 0:\n              allowed = False\n"
start = queue.index(start_marker)
end = queue.index(end_marker, start) + len(end_marker)
replacement = '''          trigger = "/claude-run"
          decision = resolve_queue_event(
              event_name=os.environ.get("EVENT_NAME", ""),
              actor=os.environ.get("ACTOR", ""),
              owner=os.environ.get("OWNER", ""),
              ref_name=os.environ.get("REF_NAME", ""),
              default_branch=os.environ.get("DEFAULT_BRANCH", ""),
              run_attempt=os.environ.get("RUN_ATTEMPT", ""),
              dispatch_issue=os.environ.get("DISPATCH_ISSUE", ""),
              dispatch_fingerprint=os.environ.get("DISPATCH_FINGERPRINT", ""),
              dispatch_attempt=os.environ.get("DISPATCH_ATTEMPT", ""),
              comment_issue=os.environ.get("COMMENT_ISSUE", ""),
              comment_body=os.environ.get("COMMENT_BODY", ""),
              comment_is_pr=os.environ.get("COMMENT_IS_PR", "false"),
          )
          number = decision.issue_number
          allowed = decision.allowed
          automated_retry = decision.automated_retry
          fingerprint_input = decision.fingerprint
          attempt_input = "" if decision.retry_attempt is None else str(decision.retry_attempt)
'''
queue = queue[:start] + replacement + queue[end:]
queue = replace_once(
    queue,
    '              fingerprint_input = os.environ.get("DISPATCH_FINGERPRINT", "").strip()\n              attempt = int(os.environ["DISPATCH_ATTEMPT"])\n',
    '              attempt = int(decision.retry_attempt or 0)\n',
    path,
)
if "EVENT_PATH" in queue or "github.event_path" in queue:
    raise RuntimeError("Queue still reads an event path")
write(path, queue)

# Bootstrap distributes only the active runtime set.
path = "bootstrap/generator.py"
generator = read(path)
generator = replace_once(
    generator,
    '    "scripts/queue_retry_identity.py", "scripts/github_api_governor.py",\n'
    '    "scripts/github_coordinator_supervisor.py", "scripts/ai_recovery_supervisor.py",\n'
    '    "scripts/supervisor_final_guard.py", "scripts/supervisor_policy.py",\n'
    '    "scripts/supervisor_runtime.py", "scripts/supervisor_queue_recovery.py",\n'
    '    "scripts/supervisor_queue_recovery_v2.py", "scripts/supervisor_queue_recovery_v3.py",\n'
    '    "scripts/foundation_drift.py",\n',
    '    "scripts/queue_retry_identity.py", "scripts/queue_event_guard.py",\n'
    '    "scripts/github_api_governor.py", "scripts/github_coordinator_supervisor.py",\n'
    '    "scripts/supervisor_policy.py", "scripts/foundation_drift.py",\n',
    path,
)
write(path, generator)

# Repository validator follows the active runtime and proves event-file independence.
path = "scripts/validate_repository.py"
validator = read(path)
validator = replace_once(
    validator,
    '    "scripts/queue_retry_identity.py", "scripts/github_api_governor.py",\n'
    '    "scripts/github_coordinator_supervisor.py", "scripts/foundation_drift.py",\n',
    '    "scripts/queue_retry_identity.py", "scripts/queue_event_guard.py",\n'
    '    "scripts/github_api_governor.py", "scripts/github_coordinator_supervisor.py",\n'
    '    "scripts/supervisor_policy.py", "scripts/foundation_drift.py",\n',
    path,
)
queue_start = validator.index('    queue = text(".github/workflows/claude-queue.yml")\n')
queue_end = validator.index('    runtime = text("scripts/github_coordinator_supervisor.py")\n', queue_start)
queue_validation = '''    queue = text(".github/workflows/claude-queue.yml")
    require(queue, (
        "from scripts.queue_event_guard import resolve_queue_event",
        "COMMENT_ISSUE:", "COMMENT_BODY:", "COMMENT_IS_PR:",
        "decision = resolve_queue_event(", "check_tool_permission_contract", "contract_ok",
        "Optional provider missing secret", "credential-isolated route unavailable",
        "provider_invocation: false", "repository_credentials_exposed: false",
        "repository_write: false", "notification: false", "human_action_required: false",
        "exit 1", "publication_route: GitHub-direct coordinator", "request_fingerprint:",
        "retry_attempt:", "automation-stops/queue-v4/issue-", "automation-internal-stops",
        'record.get("next_automatic_action") == "dispatch one optional Queue retry"',
        "should_auto_retry(failure_class, attempt - 1, 3)",
    ), "optional Queue")
    for forbidden in ("EVENT_PATH", "github.event_path", "Path(os.environ"):
        if forbidden in queue:
            raise ValidationError(f"optional Queue still reads an event payload file: {forbidden}")
    if "\\n  issues:\\n" in queue or "workflow_run:" in queue or "schedule:" in queue:
        raise ValidationError("optional Queue has an ordinary automatic trigger")
    prepare = job(queue, "prepare", "implement")
    require(prepare, (
        "resolve_queue_event", "decision.issue_number", "decision.allowed",
        "decision.automated_retry", "decision.fingerprint", "decision.retry_attempt",
        "request_fingerprint(issue, base_sha)",
    ), "optional Queue dispatch guard")
    for forbidden in ("contents: write", "issues: write", "pull-requests: write", "id-token: write"):
        if forbidden in prepare:
            raise ValidationError(f"Queue dispatch guard can write or obtain OIDC: {forbidden}")
    implement = job(queue, "implement", "verify")
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
    verify = job(queue, "verify", "publish")
    for forbidden in ("secrets.", "id-token: write", "contents: write", "pull-requests: write"):
        if forbidden in verify:
            raise ValidationError(f"verification job has forbidden capability: {forbidden}")
    publish = job(queue, "publish", "finalize")
    require(publish, ("contents: read", "repository_write: false"), "artifact publication handoff")
    for forbidden in ("contents: write", "pull-requests: write", "secrets.", "id-token: write", "anthropics/"):
        if forbidden in publish:
            raise ValidationError(f"optional publication handoff can mutate: {forbidden}")

'''
validator = validator[:queue_start] + queue_validation + validator[queue_end:]
validator = replace_once(
    validator,
    '            "scripts/queue_issue_hydration.py", "scripts/queue_retry_identity.py",\n'
    '            "scripts/github_api_governor.py", "scripts/github_coordinator_supervisor.py",\n',
    '            "scripts/queue_issue_hydration.py", "scripts/queue_retry_identity.py",\n'
    '            "scripts/queue_event_guard.py", "scripts/github_api_governor.py",\n'
    '            "scripts/github_coordinator_supervisor.py", "scripts/supervisor_policy.py",\n',
    path,
)
retired_check = '''
    if not generated_target:
        retired = (
            "scripts/ai_recovery_supervisor.py",
            "scripts/supervisor_final_guard.py",
            "scripts/supervisor_runtime.py",
            "scripts/supervisor_queue_recovery.py",
            "scripts/supervisor_queue_recovery_v2.py",
            "scripts/supervisor_queue_recovery_v3.py",
        )
        present = [relative for relative in retired if (ROOT / relative).exists()]
        if present:
            raise ValidationError("retired runtime files remain: " + ", ".join(present))

'''
insert_at = validator.index('    issue_template = text(".github/ISSUE_TEMPLATE/ai-task.yml")\n')
validator = validator[:insert_at] + retired_check + validator[insert_at:]
write(path, validator)

# Documentation records mandatory GitHub Phase 0 and optional provider status.
path = "docs/OPERATING_RULES.md"
doc = read(path).rstrip() + '''

## Active runtime and retired recovery entry points

- Mandatory Phase 0 consists of the GitHub repository connection, GitHub Actions permissions, and a validated Bootstrap installation pinned to an accepted Foundation source SHA.
- Codex and Claude setup is optional. Provider absence or exhausted provider capacity is nonblocking and must not be converted into a human-action requirement.
- Supported active runtime modules are `scripts/github_coordinator_supervisor.py`, `scripts/supervisor_policy.py`, `scripts/queue_event_guard.py`, the Queue classifier/hydration/retry-identity modules, `scripts/github_api_governor.py`, and `scripts/foundation_drift.py`.
- The former `ai_recovery_supervisor`, `supervisor_final_guard`, `supervisor_runtime`, and `supervisor_queue_recovery` v1/v2/v3 entry points are retired and are not distributed by Bootstrap.
- Queue event admission receives bounded scalar GitHub context only. It never reads `github.event_path` or an event payload file; connected source-Issue, trigger-identity, base-SHA, retry-record, exact-head review, collision, and expected-head merge checks remain fail closed.
'''
write(path, doc + "\n")

# Active-only policy/coordinator coverage replaces tests bound to retired runtime modules.
write(
    "tests/test_runtime_scope_and_checks.py",
    '''"""Active GitHub coordinator scope, check, and security contracts."""
from __future__ import annotations

import unittest
from pathlib import Path

from scripts.github_coordinator_supervisor import is_protected_path, path_allowed
from scripts.supervisor_policy import (
    declared_paths,
    protected_scope_is_authorized,
    scope_is_authorized,
)

ROOT = Path(__file__).resolve().parents[1]


class SourceScopePolicyTest(unittest.TestCase):
    def test_all_changed_and_renamed_paths_must_match_issue_allowlist(self):
        body = """
## Allowed scope
- scripts/probe.py
- tests/**

<!-- foundation-protected-authorization
paths:
- scripts/github_coordinator_supervisor.py
operation: bounded
-->
"""
        self.assertEqual(declared_paths(body), {"scripts/probe.py", "tests/**"})
        self.assertTrue(scope_is_authorized(["scripts/probe.py", "tests/unit/test_probe.py"], body))
        self.assertFalse(scope_is_authorized(["scripts/github_coordinator_supervisor.py"], body))
        self.assertFalse(scope_is_authorized(["scripts/probe.py", "README.md"], body))

    def test_protected_paths_require_independent_declarations(self):
        protected_only = """
<!-- foundation-protected-authorization
paths:
- scripts/github_coordinator_supervisor.py
operation: bounded
-->
"""
        self.assertFalse(scope_is_authorized(["scripts/github_coordinator_supervisor.py"], protected_only))
        body = """
## Allowed paths
- scripts/github_coordinator_supervisor.py

<!-- foundation-protected-authorization
paths:
- scripts/github_coordinator_supervisor.py
operation: bounded
-->
"""
        self.assertTrue(scope_is_authorized(["scripts/github_coordinator_supervisor.py"], body))
        self.assertTrue(protected_scope_is_authorized(["scripts/github_coordinator_supervisor.py"], body))

    def test_invalid_or_unbounded_path_declarations_fail_closed(self):
        body = "## Allowed paths\\n- ../outside.py\\n- *.py\\n- prose description here\\n"
        self.assertEqual(declared_paths(body), {"*.py"})
        self.assertFalse(scope_is_authorized(["README.md"], body))
        self.assertFalse(scope_is_authorized(["probe.py"], body))


class ActiveCoordinatorContractTest(unittest.TestCase):
    def test_path_matching_is_exact_or_bounded_recursive(self):
        self.assertTrue(path_allowed("tests/unit/test_probe.py", ("tests/**",)))
        self.assertTrue(path_allowed("scripts/probe.py", ("scripts/probe.py",)))
        self.assertFalse(path_allowed("scripts/probe.py.bak", ("scripts/probe.py",)))
        self.assertFalse(path_allowed("../outside", ("tests/**",)))

    def test_protected_path_families_remain_enforced(self):
        for relative in (
            ".github/workflows/ci.yml",
            "bootstrap/generator.py",
            "scripts/github_coordinator_supervisor.py",
            "scripts/supervisor_policy.py",
        ):
            self.assertTrue(is_protected_path(relative), relative)
        self.assertFalse(is_protected_path("docs/product-note.md"))

    def test_coordinator_keeps_exact_head_review_and_merge_boundaries(self):
        runtime = (ROOT / "scripts/github_coordinator_supervisor.py").read_text(encoding="utf-8")
        for required in (
            "foundation-coordinator-review",
            "foundation-protected-authorization",
            "workflow differs from the default-branch definition",
            "exact-head check evidence changed during evaluation",
            "coordinator review evidence changed during evaluation",
            "expected-head merge was rejected",
            "unresolved_threads",
            "ai-no-merge",
        ):
            self.assertIn(required, runtime)
        for forbidden in ("secrets.", "anthropics/", "claude-code-action", "id-token: write"):
            self.assertNotIn(forbidden, runtime)

    def test_supervisor_workflow_is_provider_independent(self):
        workflow = (ROOT / ".github/workflows/supervisor.yml").read_text(encoding="utf-8")
        self.assertIn("python -m scripts.github_coordinator_supervisor", workflow)
        self.assertIn('workflows: ["CI", "Unit Tests"]', workflow)
        for forbidden in ("secrets.", "anthropics/", "codex", "id-token: write", "actions: write"):
            self.assertNotIn(forbidden, workflow)


if __name__ == "__main__":
    unittest.main()
''',
)

# Queue workflow tests now bind admission to the pure scalar guard.
path = "tests/test_queue_workflow.py"
test = read(path)
test = replace_once(
    test,
    '''        self.assertIn('trigger = "/claude-run"', TEXT)
        self.assertIn("body.strip() == trigger", TEXT)
        prepare = block("prepare", "implement")
        self.assertIn("REF_NAME: ${{ github.ref_name }}", prepare)
        self.assertIn('os.environ.get("REF_NAME") == os.environ["DEFAULT_BRANCH"]', prepare)
''',
    '''        self.assertIn("from scripts.queue_event_guard import resolve_queue_event", TEXT)
        self.assertNotIn("EVENT_PATH", TEXT)
        self.assertNotIn("github.event_path", TEXT)
        prepare = block("prepare", "implement")
        for required in (
            "COMMENT_ISSUE:", "COMMENT_BODY:", "COMMENT_IS_PR:",
            "decision = resolve_queue_event(", "decision.issue_number",
            "decision.allowed", "decision.automated_retry",
        ):
            self.assertIn(required, prepare)
''',
    path,
)
test = replace_once(
    test,
    '''        for required in (
            'automation_actor = "github-actions[bot]"',
            'os.environ.get("RUN_ATTEMPT") == "1"',
            're.fullmatch(r"[0-9a-f]{20}", fingerprint_input)',
            'attempt_input in {"1", "2", "3"}',
''',
    '''        for required in (
            "from scripts.queue_event_guard import resolve_queue_event",
            "decision.fingerprint",
            "decision.retry_attempt",
''',
    path,
)
write(path, test)

path = "tests/test_queue_and_final_guard.py"
test = read(path)
test = replace_once(
    test,
    '''        self.assertIn('trigger = "/claude-run"', queue)
        self.assertIn("body.strip() == trigger", queue)
''',
    '''        self.assertIn("from scripts.queue_event_guard import resolve_queue_event", queue)
        self.assertNotIn("EVENT_PATH", queue)
        self.assertNotIn("github.event_path", queue)
''',
    path,
)
test = replace_once(
    test,
    '''            'automation_actor = "github-actions[bot]"',
            "automation-stops/queue-v4/issue-",
''',
    '''            "from scripts.queue_event_guard import resolve_queue_event",
            "decision.fingerprint",
            "decision.retry_attempt",
            "automation-stops/queue-v4/issue-",
''',
    path,
)
test = replace_once(
    test,
    '''            'RUN_ATTEMPT: ${{ github.run_attempt }}',
            'os.environ.get("RUN_ATTEMPT") == "1"',
''',
    '''            'RUN_ATTEMPT: ${{ github.run_attempt }}',
            "COMMENT_ISSUE:", "COMMENT_BODY:", "COMMENT_IS_PR:",
''',
    path,
)
test = replace_once(
    test,
    '''        self.assertIn("actor == owner and not fingerprint_input and not attempt_input", prepare)
        self.assertIn("elif actor == automation_actor", prepare)
        self.assertIn('re.fullmatch(r"[0-9a-f]{20}", fingerprint_input)', prepare)
        self.assertIn('attempt_input in {"1", "2", "3"}', prepare)
        self.assertIn("if fingerprint_input != fingerprint", prepare)
''',
    '''        self.assertIn("decision = resolve_queue_event(", prepare)
        self.assertIn("decision.allowed", prepare)
        self.assertIn("decision.automated_retry", prepare)
        self.assertIn("if fingerprint_input != fingerprint", prepare)
''',
    path,
)
write(path, test)

# Workflow security removes retired-runtime assertions and proves active/retired shape.
path = "tests/test_workflow_security.py"
test = read(path)
start = test.index("    def test_legacy_internal_stops_remain_non_commenting(self):\n")
end = test.index("    def test_guidance_and_bootstrap_keep_internal_stop_and_lock_parity(self):\n", start)
replacement = '''    def test_retired_runtime_modules_are_absent_and_active_modules_remain(self):
        retired = (
            "scripts/ai_recovery_supervisor.py",
            "scripts/supervisor_final_guard.py",
            "scripts/supervisor_runtime.py",
            "scripts/supervisor_queue_recovery.py",
            "scripts/supervisor_queue_recovery_v2.py",
            "scripts/supervisor_queue_recovery_v3.py",
        )
        for relative in retired:
            self.assertFalse((ROOT / relative).exists(), relative)
        for relative in (
            "scripts/github_coordinator_supervisor.py",
            "scripts/supervisor_policy.py",
            "scripts/queue_event_guard.py",
            "scripts/queue_failure_classifier.py",
            "scripts/queue_issue_hydration.py",
            "scripts/queue_retry_identity.py",
            "scripts/github_api_governor.py",
            "scripts/foundation_drift.py",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)

'''
test = test[:start] + replacement + test[end:]
test = replace_once(
    test,
    '''            "scripts/queue_issue_hydration.py", "scripts/queue_retry_identity.py",
            "scripts/github_api_governor.py", "destination.write_bytes(sources[relative])",
''',
    '''            "scripts/queue_issue_hydration.py", "scripts/queue_retry_identity.py",
            "scripts/queue_event_guard.py", "scripts/github_api_governor.py",
            "scripts/supervisor_policy.py", "destination.write_bytes(sources[relative])",
''',
    path,
)
write(path, test)

path = "tests/test_bootstrap.py"
test = read(path)
test = replace_once(
    test,
    '''            "scripts/queue_retry_identity.py",
            "scripts/github_api_governor.py",
''',
    '''            "scripts/queue_retry_identity.py",
            "scripts/queue_event_guard.py",
            "scripts/github_api_governor.py",
            "scripts/supervisor_policy.py",
''',
    path,
)
needle = '''        ):
            self.assertIn(required, MANAGED_FILES)

    def test_plan_only_does_not_create_target(self):
'''
replacement = '''        ):
            self.assertIn(required, MANAGED_FILES)
        for retired in (
            "scripts/ai_recovery_supervisor.py",
            "scripts/supervisor_final_guard.py",
            "scripts/supervisor_runtime.py",
            "scripts/supervisor_queue_recovery.py",
            "scripts/supervisor_queue_recovery_v2.py",
            "scripts/supervisor_queue_recovery_v3.py",
        ):
            self.assertNotIn(retired, MANAGED_FILES)

    def test_plan_only_does_not_create_target(self):
'''
test = replace_once(test, needle, replacement, path)
write(path, test)

# Delete retired source, obsolete docs, and tests that import only retired entry points.
for relative in (
    "docs/AI_RECOVERY_SUPERVISOR.md",
    "scripts/ai_recovery_supervisor.py",
    "scripts/supervisor_final_guard.py",
    "scripts/supervisor_runtime.py",
    "scripts/supervisor_queue_recovery.py",
    "scripts/supervisor_queue_recovery_v2.py",
    "scripts/supervisor_queue_recovery_v3.py",
    "tests/test_queue_recovery.py",
    "tests/test_queue_recovery_terminal_before_start.py",
    "tests/test_queue_recovery_final_races.py",
    "tests/test_queue_recovery_hardening.py",
    "tests/test_queue_reliability_e2e.py",
    "tests/test_recovery_supervisor.py",
    "tests/test_runtime_human_notice.py",
    "tests/test_trusted_run_job_attestation.py",
):
    target = ROOT / relative
    if target.exists():
        target.unlink()

# Final connected-reference audit: active code may retain protected-path metadata only.
for relative in (
    ".github/workflows/claude-queue.yml",
    "bootstrap/generator.py",
    "scripts/validate_repository.py",
):
    content = read(relative)
    if "github.event_path" in content or "EVENT_PATH" in content:
        raise RuntimeError(f"event payload dependency remains in {relative}")

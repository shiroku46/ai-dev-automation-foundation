#!/usr/bin/env python3
"""Generate the exact Issue #190 review-fix workflow blob and test patch."""
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one source match, got {count}")
    return text.replace(old, new, 1)


workflow_path = Path(".github/workflows/ci-reconcile.yml")
workflow = workflow_path.read_text(encoding="utf-8")
workflow = replace_once(
    workflow,
    "    if: github.event_name != 'workflow_run' || github.event.workflow_run.path == '.github/workflows/claude-queue.yml'\n",
    "    if: >-\n"
    "      (github.event_name == 'workflow_run' && github.event.workflow_run.path == '.github/workflows/claude-queue.yml') ||\n"
    "      github.event_name == 'schedule' ||\n"
    "      github.event_name == 'workflow_dispatch' ||\n"
    "      (github.event_name == 'issue_comment' &&\n"
    "       github.event.comment.body == '/foundation-reconcile' &&\n"
    "       github.actor == github.repository_owner &&\n"
    "       github.event.issue.pull_request == null)\n",
    "write-capable job gate",
)
workflow = replace_once(
    workflow,
    '                  "issue_not_pr": not bool(issue.get("pull_request")),\n',
    '                  "issue_not_pr": issue.get("pull_request") is None,\n',
    "strict Issue type predicate",
)
workflow = replace_once(
    workflow,
    '''                  issue = api(f"repos/{repository}/issues/{number}")
                  if not isinstance(issue, dict):
                      continue
''',
    '''                  try:
                      issue = api(f"repos/{repository}/issues/{number}")
                  except (RuntimeError, json.JSONDecodeError):
                      continue
                  if not isinstance(issue, dict):
                      continue
''',
    "bounded API read failure",
)
generated = Path("automation-tmp/issue-190-review-ci-reconcile.yml")
generated.parent.mkdir(parents=True, exist_ok=True)
generated.write_text(workflow, encoding="utf-8")

test_path = Path("tests/test_workflow_security.py")
tests = test_path.read_text(encoding="utf-8")
tests = replace_once(
    tests,
    '''            "issue_comment:\\n    types: [created]",
            "REPOSITORY_OWNER: ${{ github.repository_owner }}",
''',
    '''            "issue_comment:\\n    types: [created]",
            "github.event.comment.body == '/foundation-reconcile'",
            "github.actor == github.repository_owner",
            "github.event.issue.pull_request == null",
            "REPOSITORY_OWNER: ${{ github.repository_owner }}",
''',
    "job gate source assertions",
)
tests = replace_once(
    tests,
    '''        self.assertFalse(predicate({**exact, "pull_request": {"url": "x"}}, 173, trusted)["issue_not_pr"])
        self.assertFalse(predicate({**exact, "user": {"login": "other"}}, 173, trusted)["trusted_author"])
''',
    '''        self.assertFalse(predicate({**exact, "pull_request": {}}, 173, trusted)["issue_not_pr"])
        self.assertFalse(predicate({**exact, "pull_request": {"url": "x"}}, 173, trusted)["issue_not_pr"])
        self.assertFalse(predicate({**exact, "user": {"login": "other"}}, 173, trusted)["trusted_author"])
''',
    "strict PR object regression",
)
tests = replace_once(
    tests,
    '''        self.assertIn("time.sleep(delay)", identity)
        self.assertIn("all(last.values())", identity)
''',
    '''        self.assertIn("time.sleep(delay)", identity)
        self.assertIn("except (RuntimeError, json.JSONDecodeError)", identity)
        self.assertIn("all(last.values())", identity)
''',
    "bounded read regression",
)
test_path.write_text(tests, encoding="utf-8")

#!/usr/bin/env python3
"""Generate exact Issue #194 workflow blobs and persistent source regressions."""
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one source match, got {count}")
    return text.replace(old, new, 1)


ci_path = Path(".github/workflows/ci-reconcile.yml")
ci = ci_path.read_text(encoding="utf-8")
ci = replace_once(
    ci,
    '''          from scripts.queue_failure_classifier import (
              FailureClass,
              build_failure_status,
              classify_conclusion,
              should_auto_retry,
          )
''',
    '''          from scripts.queue_failure_classifier import (
              FailureClass,
              build_failure_status,
              classify_conclusion,
              should_auto_retry,
          )
          from scripts.queue_retry_identity import (
              exact_retry_runs,
              is_active_ignored_run,
              is_active_queue_run,
              parse_queue_run_title,
              retry_run_title,
              stranded_retry_attempt,
              validate_retry_records,
          )
''',
    "retry identity imports",
)
ci = replace_once(
    ci,
    '''          def active_queue_run() -> bool:
              runs = api_pages(f"repos/{repository}/actions/workflows/{queue_workflow}/runs?per_page=100", "workflow_runs")
              return any(
                  str(run.get("path") or "").split("@", 1)[0] == ".github/workflows/claude-queue.yml"
                  and run.get("head_branch") == default_branch
                  and str(run.get("status") or "") in {"queued", "in_progress", "waiting", "pending", "requested"}
                  for run in runs
              )
''',
    '''          def queue_runs() -> list[dict[str, Any]]:
              return api_pages(
                  f"repos/{repository}/actions/workflows/{queue_workflow}/runs?per_page=100",
                  "workflow_runs",
              )

          def active_queue_run() -> bool:
              return any(
                  is_active_queue_run(run)
                  and run.get("head_branch") == default_branch
                  and ((run.get("repository") or {}).get("full_name") or repository) == repository
                  for run in queue_runs()
              )

          def wait_for_control_noop(issue_number: int) -> bool:
              for delay in (0.0, 1.0, 2.0, 3.0, 4.0):
                  if delay:
                      time.sleep(delay)
                  active = any(
                      is_active_ignored_run(run, issue_number)
                      and run.get("head_branch") == default_branch
                      and ((run.get("repository") or {}).get("full_name") or repository) == repository
                      for run in queue_runs()
                  )
                  if not active:
                      return True
              return False

          def exact_retry_run(
              issue_number: int,
              attempt: int,
              fingerprint: str,
              base_sha: str,
              runs: list[dict[str, Any]] | None = None,
          ) -> dict[str, Any] | None:
              matches = exact_retry_runs(
                  queue_runs() if runs is None else runs,
                  repository=repository,
                  default_branch=default_branch,
                  base_sha=base_sha,
                  issue_number=issue_number,
                  attempt=attempt,
                  fingerprint=fingerprint,
              )
              return dict(matches[0]) if matches else None

          def confirm_retry_run(
              issue_number: int,
              attempt: int,
              fingerprint: str,
              base_sha: str,
          ) -> dict[str, Any]:
              for delay in (0.0, 0.5, 1.0, 2.0, 4.0):
                  if delay:
                      time.sleep(delay)
                  run = exact_retry_run(issue_number, attempt, fingerprint, base_sha)
                  if run is not None:
                      return run
              raise RuntimeError("exact Queue retry run was not connected after dispatch")
''',
    "Queue run identity helpers",
)
old_retry = '''          def retry_records(issue_number: int, fingerprint: str) -> list[dict[str, Any]]:
              root = record_path(issue_number, fingerprint, "").rstrip("/")
              encoded = urllib.parse.quote(root, safe="/")
              result = subprocess.run(
                  ["gh", "api", f"repos/{repository}/contents/{encoded}?ref={stop_branch}"],
                  capture_output=True, text=True, check=False,
              )
              if result.returncode != 0:
                  if "404" in (result.stdout + result.stderr):
                      return []
                  raise RuntimeError("could not list recovery records")
              items = json.loads(result.stdout)
              records = []
              for item in items:
                  match = re.fullmatch(r"retry-([1-9][0-9]*)\\.json", str(item.get("name") or ""))
                  if match:
                      value = read_record(f"{root}/{item['name']}")
                      if value is not None:
                          records.append(value)
              return sorted(records, key=lambda item: int(item.get("attempt") or 0))

          def dispatch_retry(issue: dict[str, Any], base_sha: str, failure_class: FailureClass, source_run_id: int | None) -> bool:
              issue_number = int(issue["number"])
              fingerprint = request_fingerprint(issue, base_sha)
              if any(
                  read_record(record_path(issue_number, fingerprint, name)) is not None
                  for name in ("internal-stop.json", "exhausted.json")
              ):
                  return False
              records = retry_records(issue_number, fingerprint)
              attempt = len(records) + 1
              status = build_failure_status(
                  failure_class=failure_class,
                  retry_attempt=len(records),
                  max_retries=max_retries,
                  checkpoint_sha=None,
              )
              if not should_auto_retry(failure_class, len(records), max_retries):
                  name = "exhausted.json" if len(records) >= max_retries else "internal-stop.json"
                  next_action = (
                      "optional provider route unavailable; continue GitHub-direct work"
                      if failure_class is FailureClass.AUTH_SECRET
                      else status.next_automatic_action
                  )
                  put_record(
                      record_path(issue_number, fingerprint, name),
                      {
                          "attempt": len(records),
                          "base_sha": base_sha,
                          "failure_class": status.failure_class.value,
                          "human_action_required": False,
                          "issue_number": issue_number,
                          "next_automatic_action": next_action,
                          "notification": False,
                          "request_fingerprint": fingerprint,
                          "source_run_id": source_run_id,
                      },
                      f"Record Queue recovery stop for Issue #{issue_number}",
                      base_sha,
                  )
                  return False
              payload = {
                  "attempt": attempt,
                  "base_sha": base_sha,
                  "failure_class": failure_class.value,
                  "human_action_required": False,
                  "issue_number": issue_number,
                  "next_automatic_action": "dispatch one optional Queue retry",
                  "notification": False,
                  "request_fingerprint": fingerprint,
                  "source_run_id": source_run_id,
              }
              path = record_path(issue_number, fingerprint, f"retry-{attempt}.json")
              if not put_record(path, payload, f"Record Queue retry {attempt} for Issue #{issue_number}", base_sha):
                  return False
              if current_default_sha() != base_sha or active_queue_run() or candidate_for_issue(issue_number):
                  return False
              gh(
                  "workflow", "run", queue_workflow,
                  "--repo", repository,
                  "--ref", default_branch,
                  "-f", f"issue_number={issue_number}",
                  "-f", f"request_fingerprint={fingerprint}",
                  "-f", f"retry_attempt={attempt}",
              )
              return True
'''
new_retry = '''          def retry_records(
              issue_number: int,
              fingerprint: str,
              base_sha: str,
          ) -> list[dict[str, Any]]:
              root = record_path(issue_number, fingerprint, "").rstrip("/")
              encoded = urllib.parse.quote(root, safe="/")
              result = subprocess.run(
                  ["gh", "api", f"repos/{repository}/contents/{encoded}?ref={stop_branch}"],
                  capture_output=True, text=True, check=False,
              )
              if result.returncode != 0:
                  if "404" in (result.stdout + result.stderr):
                      return []
                  raise RuntimeError("could not list recovery records")
              items = json.loads(result.stdout)
              records = []
              for item in items:
                  match = re.fullmatch(r"retry-([1-9][0-9]*)\\.json", str(item.get("name") or ""))
                  if match:
                      value = read_record(f"{root}/{item['name']}")
                      if value is not None:
                          records.append(value)
              records = sorted(records, key=lambda item: int(item.get("attempt") or 0))
              return list(validate_retry_records(
                  records,
                  issue_number=issue_number,
                  base_sha=base_sha,
                  fingerprint=fingerprint,
                  max_retries=max_retries,
              ))

          def dispatch_retry(issue: dict[str, Any], base_sha: str, failure_class: FailureClass, source_run_id: int | None) -> bool:
              issue_number = int(issue["number"])
              fingerprint = request_fingerprint(issue, base_sha)
              if any(
                  read_record(record_path(issue_number, fingerprint, name)) is not None
                  for name in ("internal-stop.json", "exhausted.json")
              ):
                  return False

              records = retry_records(issue_number, fingerprint, base_sha)
              connected_runs = queue_runs()
              dispatched_attempts: list[int] = []
              for record in records:
                  attempt_value = int(record["attempt"])
                  if exact_retry_run(
                      issue_number,
                      attempt_value,
                      fingerprint,
                      base_sha,
                      connected_runs,
                  ) is not None:
                      dispatched_attempts.append(attempt_value)
              stranded = stranded_retry_attempt(records, dispatched_attempts)

              if stranded is not None:
                  attempt = stranded
                  payload = dict(records[attempt - 1])
              else:
                  if active_queue_run() or candidate_for_issue(issue_number):
                      return False
                  attempt = len(records) + 1
                  status = build_failure_status(
                      failure_class=failure_class,
                      retry_attempt=len(records),
                      max_retries=max_retries,
                      checkpoint_sha=None,
                  )
                  if not should_auto_retry(failure_class, len(records), max_retries):
                      name = "exhausted.json" if len(records) >= max_retries else "internal-stop.json"
                      next_action = (
                          "optional provider route unavailable; continue GitHub-direct work"
                          if failure_class is FailureClass.AUTH_SECRET
                          else status.next_automatic_action
                      )
                      put_record(
                          record_path(issue_number, fingerprint, name),
                          {
                              "attempt": len(records),
                              "base_sha": base_sha,
                              "failure_class": status.failure_class.value,
                              "human_action_required": False,
                              "issue_number": issue_number,
                              "next_automatic_action": next_action,
                              "notification": False,
                              "request_fingerprint": fingerprint,
                              "source_run_id": source_run_id,
                          },
                          f"Record Queue recovery stop for Issue #{issue_number}",
                          base_sha,
                      )
                      return False
                  payload = {
                      "attempt": attempt,
                      "base_sha": base_sha,
                      "failure_class": failure_class.value,
                      "human_action_required": False,
                      "issue_number": issue_number,
                      "next_automatic_action": "dispatch one optional Queue retry",
                      "notification": False,
                      "request_fingerprint": fingerprint,
                      "source_run_id": source_run_id,
                  }
                  path = record_path(issue_number, fingerprint, f"retry-{attempt}.json")
                  put_record(
                      path,
                      payload,
                      f"Record Queue retry {attempt} for Issue #{issue_number}",
                      base_sha,
                  )
                  visible = read_record(path)
                  if visible != payload:
                      raise RuntimeError("retry record was not object-identical before dispatch")
                  records = retry_records(issue_number, fingerprint, base_sha)
                  if len(records) != attempt or records[-1] != payload:
                      raise RuntimeError("retry record collection changed before dispatch")

              if exact_retry_run(issue_number, attempt, fingerprint, base_sha) is not None:
                  return False
              if current_default_sha() != base_sha or candidate_for_issue(issue_number):
                  return False
              if event_name == "issue_comment" and not wait_for_control_noop(issue_number):
                  return False
              if active_queue_run():
                  return False
              gh(
                  "workflow", "run", queue_workflow,
                  "--repo", repository,
                  "--ref", default_branch,
                  "-f", f"issue_number={issue_number}",
                  "-f", f"request_fingerprint={fingerprint}",
                  "-f", f"retry_attempt={attempt}",
              )
              confirmed = confirm_retry_run(issue_number, attempt, fingerprint, base_sha)
              if confirmed.get("display_title") != retry_run_title(issue_number, attempt, fingerprint):
                  raise RuntimeError("confirmed Queue retry run identity changed")
              return True
'''
ci = replace_once(ci, old_retry, new_retry, "stranded retry dispatch")
ci = replace_once(
    ci,
    '''          def issue_from_run(run: dict[str, Any]) -> dict[str, Any]:
              title = str(run.get("display_title") or "")
              match = re.fullmatch(r"Optional Claude issue-([1-9][0-9]*)", title)
              if match is None:
                  raise RuntimeError("Queue run title does not identify exactly one Issue")
              return trusted_issue(int(match.group(1)))
''',
    '''          def issue_from_run(run: dict[str, Any]) -> dict[str, Any]:
              identity = parse_queue_run_title(run.get("display_title"))
              if identity is None or identity["kind"] == "ignored":
                  raise RuntimeError("Queue run title does not identify one executable Issue")
              return trusted_issue(int(identity["issue_number"]))
''',
    "Queue run title parsing",
)
ci = replace_once(
    ci,
    '''                      run for run in runs
                      if run.get("display_title") == f"Optional Claude issue-{issue_number}"
                      and str(run.get("path") or "").split("@", 1)[0] == ".github/workflows/claude-queue.yml"
''',
    '''                      run for run in runs
                      if (parse_queue_run_title(run.get("display_title")) or {}).get("issue_number") == issue_number
                      and (parse_queue_run_title(run.get("display_title")) or {}).get("kind") != "ignored"
                      and str(run.get("path") or "").split("@", 1)[0] == ".github/workflows/claude-queue.yml"
''',
    "artifact run title filter",
)
old_workflow_run = '''          if event_name == "workflow_run":
              run = event.get("workflow_run") or {}
              if (
                  str(run.get("path") or "").split("@", 1)[0] != ".github/workflows/claude-queue.yml"
                  or run.get("head_branch") != default_branch
                  or run.get("status") != "completed"
                  or ((run.get("repository") or {}).get("full_name") or repository) != repository
              ):
                  raise RuntimeError("workflow_run is not the fixed Queue completion")
              selected_issue = issue_from_run(run)
              source_run_id = int(run.get("id") or 0)
              artifact = verify_artifact(run, selected_issue)
              if artifact is not None:
                  resumed = resume_remote_branch(selected_issue, base_sha)
                  if resumed is not None:
                      action = f"resumed_pr_{resumed}"
                  else:
                      pr_number = publish_artifact(run, selected_issue, artifact)
                      action = f"published_pr_{pr_number}"
              elif not candidate_for_issue(int(selected_issue["number"])):
                  failure_class = failure_class_for_run(run)
                  if dispatch_retry(selected_issue, base_sha, failure_class, source_run_id):
                      action = "retry_dispatched"
                  else:
                      action = f"stopped_{failure_class.value}"
          elif event_name == "issue_comment":
              selected_issue = issue_from_control_event()
              if not active_queue_run() and not candidate_for_issue(int(selected_issue["number"])):
                  resumed = resume_remote_branch(selected_issue, base_sha)
                  if resumed is not None:
                      action = f"resumed_pr_{resumed}"
                  else:
                      recovered = latest_verified_artifact(selected_issue, base_sha)
                      if recovered is not None:
                          recovered_run, artifact = recovered
                          source_run_id = int(recovered_run.get("id") or 0)
                          pr_number = publish_artifact(recovered_run, selected_issue, artifact)
                          action = f"published_pr_{pr_number}"
                      elif dispatch_retry(selected_issue, base_sha, FailureClass.UNKNOWN, None):
                          action = "control_retry_dispatched"
                      else:
                          action = "control_already_recorded"
'''
new_workflow_run = '''          if event_name == "workflow_run":
              run = event.get("workflow_run") or {}
              if (
                  str(run.get("path") or "").split("@", 1)[0] != ".github/workflows/claude-queue.yml"
                  or run.get("head_branch") != default_branch
                  or run.get("status") != "completed"
                  or ((run.get("repository") or {}).get("full_name") or repository) != repository
              ):
                  raise RuntimeError("workflow_run is not the fixed Queue completion")
              run_identity = parse_queue_run_title(run.get("display_title"))
              if run_identity is None:
                  raise RuntimeError("Queue completion title is invalid")
              if run_identity["kind"] == "ignored":
                  selected_issue = trusted_issue(int(run_identity["issue_number"]))
                  source_run_id = int(run.get("id") or 0)
                  action = "ignored_control_noop"
              else:
                  selected_issue = issue_from_run(run)
                  source_run_id = int(run.get("id") or 0)
                  artifact = verify_artifact(run, selected_issue)
                  if artifact is not None:
                      resumed = resume_remote_branch(selected_issue, base_sha)
                      if resumed is not None:
                          action = f"resumed_pr_{resumed}"
                      else:
                          pr_number = publish_artifact(run, selected_issue, artifact)
                          action = f"published_pr_{pr_number}"
                  elif not candidate_for_issue(int(selected_issue["number"])):
                      failure_class = failure_class_for_run(run)
                      if dispatch_retry(selected_issue, base_sha, failure_class, source_run_id):
                          action = "retry_dispatched"
                      else:
                          action = f"stopped_{failure_class.value}"
          elif event_name == "issue_comment":
              selected_issue = issue_from_control_event()
              if not candidate_for_issue(int(selected_issue["number"])):
                  resumed = resume_remote_branch(selected_issue, base_sha)
                  if resumed is not None:
                      action = f"resumed_pr_{resumed}"
                  else:
                      recovered = latest_verified_artifact(selected_issue, base_sha)
                      if recovered is not None:
                          recovered_run, artifact = recovered
                          source_run_id = int(recovered_run.get("id") or 0)
                          pr_number = publish_artifact(recovered_run, selected_issue, artifact)
                          action = f"published_pr_{pr_number}"
                      elif dispatch_retry(selected_issue, base_sha, FailureClass.UNKNOWN, None):
                          action = "control_retry_dispatched"
                      else:
                          action = "control_already_recorded"
'''
ci = replace_once(ci, old_workflow_run, new_workflow_run, "workflow completion and control flow")
Path("automation-tmp/issue-194-ci-reconcile.yml").parent.mkdir(parents=True, exist_ok=True)
Path("automation-tmp/issue-194-ci-reconcile.yml").write_text(ci, encoding="utf-8")

queue_path = Path(".github/workflows/claude-queue.yml")
queue = queue_path.read_text(encoding="utf-8")
queue = replace_once(
    queue,
    "run-name: Optional Claude issue-${{ inputs.issue_number || github.event.issue.number || 'event' }}\n",
    "run-name: >-\n"
    "  ${{ inputs.request_fingerprint && format('Optional Claude issue-{0} retry-{1} request-{2}', inputs.issue_number, inputs.retry_attempt, inputs.request_fingerprint) || (github.event_name == 'workflow_dispatch' && format('Optional Claude issue-{0} manual', inputs.issue_number) || (github.event.comment.body == '/claude-run' && format('Optional Claude issue-{0} trigger', github.event.issue.number) || format('Optional Claude ignored issue-{0}', github.event.issue.number || 'event'))) }}\n",
    "exact Queue run name",
)
queue = replace_once(
    queue,
    '''            Turn budget: reserve the final 5 turns for a path audit and durable WIP checkpoint.
            At the reserve threshold, stop editing and leave the best authorized worktree state.
''',
    '''            Automated retry attempt: `${{ inputs.retry_attempt || 'none' }}`.
            When the value is 1, 2, or 3, make the exact authorized implementation edit before broad exploration.
            Use only Read, Write, Edit, Glob, and Grep. Do not attempt Bash, git commands, or test execution.
            Trusted read-only verification runs after checkpoint creation.
            For an automated retry, reserve the final 8 turns for an exact path audit and durable checkpoint.
            For an owner-started route, reserve the final 5 turns for the same audit and checkpoint.
            Do not change any path outside the Issue allowlist.
            At the reserve threshold, stop editing and leave the best authorized worktree state.
''',
    "bounded retry prompt",
)
Path("automation-tmp/issue-194-claude-queue.yml").write_text(queue, encoding="utf-8")

security_path = Path("tests/test_workflow_security.py")
security = security_path.read_text(encoding="utf-8")
marker = "    def test_supervisor_is_default_branch_github_coordinator_only(self):\n"
new_security_tests = '''    def test_exact_retry_identity_and_stranded_dispatch_source(self):
        reconcile = read(".github/workflows/ci-reconcile.yml")
        for required in (
            "from scripts.queue_retry_identity import (",
            "exact_retry_runs",
            "parse_queue_run_title",
            "stranded_retry_attempt",
            "validate_retry_records",
            "def wait_for_control_noop",
            "for delay in (0.0, 1.0, 2.0, 3.0, 4.0)",
            "def confirm_retry_run",
            "for delay in (0.0, 0.5, 1.0, 2.0, 4.0)",
            "stranded = stranded_retry_attempt(records, dispatched_attempts)",
            "attempt = stranded",
            "payload = dict(records[attempt - 1])",
            "put_record(",
            "visible = read_record(path)",
            "exact_retry_run(issue_number, attempt, fingerprint, base_sha)",
            'event_name == "issue_comment" and not wait_for_control_noop(issue_number)',
            'action = "ignored_control_noop"',
            "confirmed = confirm_retry_run",
        ):
            self.assertIn(required, reconcile)
        dispatch = reconcile.split("          def dispatch_retry(", 1)[1].split(
            "\\n          def failure_class_for_run(", 1
        )[0]
        self.assertNotIn("if not put_record", dispatch)
        self.assertIn("max_retries=max_retries", dispatch)
        self.assertNotIn("max_retries = 4", reconcile)
        self.assertNotIn("force=True", dispatch)
        self.assertNotIn('"force": True', dispatch)

''' + marker
security = replace_once(security, marker, new_security_tests, "retry dispatch source tests")
security_path.write_text(security, encoding="utf-8")

queue_test_path = Path("tests/test_queue_workflow.py")
queue_tests = queue_test_path.read_text(encoding="utf-8")
marker = "    def test_permission_preflight_is_before_provider(self):\n"
new_queue_tests = '''    def test_run_names_bind_retry_identity_and_ignore_control_comments(self):
        for required in (
            "format('Optional Claude issue-{0} retry-{1} request-{2}'",
            "format('Optional Claude issue-{0} manual'",
            "format('Optional Claude issue-{0} trigger'",
            "format('Optional Claude ignored issue-{0}'",
        ):
            self.assertIn(required, TEXT)
        self.assertNotIn("run-name: Optional Claude issue-${{ inputs.issue_number", TEXT)

    def test_automated_retry_prompt_is_edit_first_and_bounded(self):
        implement = block("implement", "verify")
        for required in (
            "Automated retry attempt:",
            "make the exact authorized implementation edit before broad exploration",
            "Use only Read, Write, Edit, Glob, and Grep",
            "Do not attempt Bash, git commands, or test execution",
            "Trusted read-only verification runs after checkpoint creation",
            "reserve the final 8 turns",
            "Do not change any path outside the Issue allowlist",
            "--max-turns 40",
            '--allowedTools "Read,Write,Edit,Glob,Grep"',
        ):
            self.assertIn(required, implement)
        self.assertNotIn("--max-turns 41", implement)

''' + marker
queue_tests = replace_once(queue_tests, marker, new_queue_tests, "retry title and prompt tests")
queue_test_path.write_text(queue_tests, encoding="utf-8")

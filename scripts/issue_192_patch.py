#!/usr/bin/env python3
"""Generate exact Issue #192 workflow blobs and persistent tests."""
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one source match, got {count}")
    return text.replace(old, new, 1)


# Reconciliation workflow: accept an object-identical record across a verified
# linear single-parent descendant append instead of requiring head equality.
ci_path = Path(".github/workflows/ci-reconcile.yml")
ci = ci_path.read_text(encoding="utf-8")
old_visibility = '''          def wait_for_record(path: str, payload: dict[str, Any], expected_commit: str | None) -> bool:
              quoted_branch = urllib.parse.quote(stop_branch, safe="")
              visibility_delays = (0.0, 0.5, 1.0, 2.0)
              for delay in visibility_delays:
                  if delay:
                      time.sleep(delay)
                  if expected_commit is not None:
                      current_ref = api(f"repos/{repository}/git/ref/heads/{quoted_branch}")
                      current_sha = str((current_ref.get("object") or {}).get("sha") or "")
                      if current_sha != expected_commit:
                          raise RuntimeError("recovery record ref moved during visibility verification")
                  visible = read_record(path)
                  if visible is None:
                      continue
                  if visible != payload:
                      raise RuntimeError("deterministic recovery record identity changed")
                  return True
              return False
'''
new_visibility = '''          def linear_descendant(
              current_sha: str,
              expected_sha: str,
              fetch_commit,
              max_depth: int = 32,
          ) -> bool:
              sha_pattern = re.compile(r"^[0-9a-f]{40}$")
              if (
                  not sha_pattern.fullmatch(current_sha)
                  or not sha_pattern.fullmatch(expected_sha)
                  or isinstance(max_depth, bool)
                  or not isinstance(max_depth, int)
                  or max_depth < 0
                  or max_depth > 32
              ):
                  return False
              cursor = current_sha
              visited: set[str] = set()
              for depth in range(max_depth + 1):
                  if cursor == expected_sha:
                      return True
                  if depth == max_depth or cursor in visited:
                      return False
                  visited.add(cursor)
                  commit = fetch_commit(cursor)
                  if not isinstance(commit, dict) or commit.get("sha") != cursor:
                      return False
                  parents = commit.get("parents")
                  if not isinstance(parents, list) or len(parents) != 1:
                      return False
                  parent = parents[0]
                  parent_sha = str(parent.get("sha") or "") if isinstance(parent, dict) else ""
                  if not sha_pattern.fullmatch(parent_sha):
                      return False
                  cursor = parent_sha
              return False

          def stop_ref_sha() -> str:
              quoted_branch = urllib.parse.quote(stop_branch, safe="")
              current_ref = api(f"repos/{repository}/git/ref/heads/{quoted_branch}")
              current_sha = str((current_ref.get("object") or {}).get("sha") or "")
              if not exact_sha.fullmatch(current_sha):
                  raise RuntimeError("recovery record ref identity is invalid")
              return current_sha

          def ref_linearly_contains(current_sha: str, expected_sha: str) -> bool:
              return linear_descendant(
                  current_sha,
                  expected_sha,
                  lambda sha: api(f"repos/{repository}/git/commits/{sha}"),
                  32,
              )

          def wait_for_record(path: str, payload: dict[str, Any], expected_ancestor: str) -> bool:
              visibility_delays = (0.0, 0.5, 1.0, 2.0)
              for delay in visibility_delays:
                  if delay:
                      time.sleep(delay)
                  before_sha = stop_ref_sha()
                  if not ref_linearly_contains(before_sha, expected_ancestor):
                      raise RuntimeError("recovery record ref is not a linear descendant")
                  visible = read_record(path)
                  after_sha = stop_ref_sha()
                  if not ref_linearly_contains(after_sha, expected_ancestor):
                      raise RuntimeError("recovery record ref changed outside the linear history")
                  if visible is None:
                      continue
                  if visible != payload:
                      raise RuntimeError("deterministic recovery record identity changed")
                  return True
              return False
'''
ci = replace_once(ci, old_visibility, new_visibility, "linear visibility implementation")
ci = replace_once(
    ci,
    '''              except RuntimeError:
                  if wait_for_record(path, payload, None):
                      return False
                  raise RuntimeError("recovery record compare-and-swap failed")
''',
    '''              except RuntimeError:
                  if wait_for_record(path, payload, observed_head):
                      return False
                  raise RuntimeError("recovery record compare-and-swap failed")
''',
    "uncertain CAS ancestry",
)
Path("automation-tmp/issue-192-ci-reconcile.yml").parent.mkdir(parents=True, exist_ok=True)
Path("automation-tmp/issue-192-ci-reconcile.yml").write_text(ci, encoding="utf-8")

# Queue workflow: allow only the exact GitHub Actions bot behind the existing
# connected retry-record guard.
queue_path = Path(".github/workflows/claude-queue.yml")
queue = queue_path.read_text(encoding="utf-8")
queue = replace_once(
    queue,
    '''          branch_prefix: claude-issue-${{ needs.prepare.outputs.issue_number }}-
          track_progress: false
          prompt: |
''',
    '''          branch_prefix: claude-issue-${{ needs.prepare.outputs.issue_number }}-
          track_progress: false
          allowed_bots: github-actions
          prompt: |
''',
    "literal retry bot allowlist",
)
Path("automation-tmp/issue-192-claude-queue.yml").write_text(queue, encoding="utf-8")

# Repository-wide security tests.
security_path = Path("tests/test_workflow_security.py")
security = security_path.read_text(encoding="utf-8")
security = replace_once(
    security,
    '    namespace: dict[str, Any] = {"Any": Any}\n',
    '    namespace: dict[str, Any] = {"Any": Any, "re": re}\n',
    "embedded function namespace",
)
security = replace_once(
    security,
    '''            "id-token: write", "persist-credentials: false", "track_progress: false",
''',
    '''            "id-token: write", "persist-credentials: false", "track_progress: false",
            "allowed_bots: github-actions",
''',
    "provider literal bot assertion",
)
old_cas_assertions = '''            'wait_for_record(path, payload, None)',
            'wait_for_record(path, payload, commit_sha)',
'''
new_cas_assertions = '''            'wait_for_record(path, payload, observed_head)',
            'wait_for_record(path, payload, commit_sha)',
            'linear_descendant(',
            'ref_linearly_contains(',
'''
security = replace_once(security, old_cas_assertions, new_cas_assertions, "CAS ancestry assertions")
old_visibility_test = '''        visibility = reconcile.split("          def wait_for_record(", 1)[1].split(
            "\\n          def put_record(", 1
        )[0]
        self.assertIn("visibility_delays = (0.0, 0.5, 1.0, 2.0)", visibility)
        self.assertIn("time.sleep(delay)", visibility)
        self.assertIn("current_sha != expected_commit", visibility)
        self.assertIn("visible != payload", visibility)
        self.assertIn("return True", visibility)
        self.assertIn("return False", visibility)
        self.assertLessEqual(sum((0.0, 0.5, 1.0, 2.0)), 6.0)
        self.assertEqual(len((0.0, 0.5, 1.0, 2.0)), 4)
'''
new_visibility_test = '''        visibility = reconcile.split("          def wait_for_record(", 1)[1].split(
            "\\n          def put_record(", 1
        )[0]
        self.assertIn("visibility_delays = (0.0, 0.5, 1.0, 2.0)", visibility)
        self.assertIn("time.sleep(delay)", visibility)
        self.assertIn("before_sha = stop_ref_sha()", visibility)
        self.assertIn("after_sha = stop_ref_sha()", visibility)
        self.assertIn("ref_linearly_contains(before_sha, expected_ancestor)", visibility)
        self.assertIn("ref_linearly_contains(after_sha, expected_ancestor)", visibility)
        self.assertIn("visible != payload", visibility)
        self.assertIn("return True", visibility)
        self.assertIn("return False", visibility)
        self.assertLessEqual(sum((0.0, 0.5, 1.0, 2.0)), 6.0)
        self.assertEqual(len((0.0, 0.5, 1.0, 2.0)), 4)

    def test_queue_recovery_linear_descendant_fixtures(self):
        reconcile = read(".github/workflows/ci-reconcile.yml")
        linear = embedded_function(reconcile, "linear_descendant")
        sha = lambda character: character * 40
        root, one, two, three = (sha(value) for value in "abcd")

        def fetch(graph):
            return lambda value: graph[value]

        self.assertTrue(linear(root, root, lambda value: (_ for _ in ()).throw(AssertionError(value))))
        one_graph = {one: {"sha": one, "parents": [{"sha": root}]}}
        self.assertTrue(linear(one, root, fetch(one_graph)))
        multi_graph = {
            three: {"sha": three, "parents": [{"sha": two}]},
            two: {"sha": two, "parents": [{"sha": one}]},
            one: {"sha": one, "parents": [{"sha": root}]},
        }
        self.assertTrue(linear(three, root, fetch(multi_graph)))
        cycle_graph = {
            one: {"sha": one, "parents": [{"sha": two}]},
            two: {"sha": two, "parents": [{"sha": one}]},
        }
        self.assertFalse(linear(one, root, fetch(cycle_graph)))
        self.assertFalse(linear(one, root, fetch({one: {"sha": one, "parents": [{"sha": root}, {"sha": two}]}})))
        self.assertFalse(linear(one, root, fetch({one: {"sha": one, "parents": []}})))
        self.assertFalse(linear("bad", root, fetch({})))
        self.assertFalse(linear(one, root, fetch({one: {"sha": two, "parents": [{"sha": root}]}})))

        chain = [f"{index:040x}" for index in range(35)]
        graph = {
            chain[index]: {"sha": chain[index], "parents": [{"sha": chain[index - 1]}]}
            for index in range(1, len(chain))
        }
        self.assertFalse(linear(chain[-1], chain[0], fetch(graph), 32))
        self.assertTrue(linear(chain[-1], chain[0], fetch(graph), 34))
'''
security = replace_once(security, old_visibility_test, new_visibility_test, "linear ancestry fixtures")
security_path.write_text(security, encoding="utf-8")

# Queue-specific tests.
queue_test_path = Path("tests/test_queue_workflow.py")
queue_tests = queue_test_path.read_text(encoding="utf-8")
queue_tests = replace_once(
    queue_tests,
    '''            "id-token: write", "persist-credentials: false", "track_progress: false",
            "reserve the final 5 turns", '--allowedTools "Read,Write,Edit,Glob,Grep"',
''',
    '''            "id-token: write", "persist-credentials: false", "track_progress: false",
            "allowed_bots: github-actions",
            "reserve the final 5 turns", '--allowedTools "Read,Write,Edit,Glob,Grep"',
''',
    "Queue provider bot assertion",
)
queue_tests = replace_once(
    queue_tests,
    '''        for forbidden in ("contents: write", "issues: write", "pull-requests: write", "track_progress: true"):
            self.assertNotIn(forbidden, implement)
''',
    '''        for forbidden in ("contents: write", "issues: write", "pull-requests: write", "track_progress: true"):
            self.assertNotIn(forbidden, implement)
        self.assertEqual(implement.count("allowed_bots: github-actions"), 1)
        self.assertNotIn("allowed_bots: *", implement)
        self.assertNotRegex(implement, r"allowed_bots:\\s*\\$\\{\\{")

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
''',
    "exact automated retry guard test",
)
queue_test_path.write_text(queue_tests, encoding="utf-8")

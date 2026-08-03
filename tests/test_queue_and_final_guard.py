import base64
import importlib
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.supervisor_policy import is_protected

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHA = "d" * 40
CANDIDATE_SHA = "c" * 40
ISSUE_NUMBER = 85


class QueueAndFinalGuardTest(unittest.TestCase):
    @staticmethod
    def _queue_workflow():
        return (ROOT / ".github/workflows/claude-queue.yml").read_text(encoding="utf-8")

    @classmethod
    def _queue_python_step(cls, step_name):
        queue = cls._queue_workflow()
        step = queue.split(f"- name: {step_name}", 1)[1]
        source = step.split("python3 - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
        return textwrap.dedent(source)

    def test_queue_exact_existing_pr_base_contract_is_fail_closed(self):
        queue = self._queue_workflow()
        self.assertIn("foundation-queue-existing-pr-base", queue)
        self.assertIn('set(values) != {"pull_request", "base_ref", "base_sha"}', queue)
        self.assertIn('pull.get("state") == "open"', queue)
        self.assertIn('head_repo.get("full_name") == repository', queue)
        self.assertIn('head.get("ref") == values["base_ref"]', queue)
        self.assertIn('head.get("sha") == values["base_sha"]', queue)
        self.assertIn("base moved before publication", queue)

    def test_queue_uses_full_byte_handoff_and_api_only_publication(self):
        queue = self._queue_workflow()
        implement = queue.split("\n  implement:\n", 1)[1].split("\n  verify:\n", 1)[0]
        publish = queue.split("\n  publish:\n", 1)[1].split("\n  finalize:\n", 1)[0]
        self.assertIn("persist-credentials: false", implement)
        self.assertIn("contents: read", implement)
        self.assertNotIn("contents: write", implement)
        self.assertIn("content_base64", implement)
        self.assertIn("artifact_sha256", implement)
        self.assertIn("actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02", implement)
        self.assertIn("git\", \"ls-files\", \"--others\", \"--exclude-standard\", \"-z", implement)
        self.assertIn("stat.S_ISREG", implement)
        self.assertIn("for path in sorted(records_by_path)", implement)
        self.assertIn("contents: write", publish)
        self.assertIn("repos/{repo}/git/blobs", publish)
        self.assertIn("repos/{repo}/git/trees", publish)
        self.assertIn("repos/{repo}/git/commits", publish)
        self.assertIn("repos/{repo}/git/commits/{base_sha}", publish)
        self.assertIn('cmd += ["--input", "-"]', publish)
        self.assertIn('"parents": [base_sha]', publish)
        self.assertIn('"draft": True', publish)
        self.assertNotIn("json.dumps(tree", publish)
        self.assertNotIn("json.dumps([base_sha])", publish)
        self.assertNotIn("actions/checkout", publish)

    def test_queue_packages_untracked_file_byte_completely(self):
        script = self._queue_python_step("Package complete candidate bytes and manifest")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Queue Test"], cwd=root, check=True)
            (root / "tracked.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
            base_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()

            payload = b"new file\x00with exact bytes\xff\n"
            (root / "new.bin").write_bytes(payload)
            runner_temp = Path(directory) / "runner"
            runner_temp.mkdir()
            output = Path(directory) / "github-output"
            environment = os.environ.copy()
            environment.update(
                {
                    "BASE_SHA": base_sha,
                    "ISSUE_NUMBER": "135",
                    "CLAUDE_BRANCH": "claude-issue-135-test",
                    "RUNNER_TEMP": str(runner_temp),
                    "GITHUB_OUTPUT": str(output),
                }
            )
            subprocess.run([sys.executable, "-c", script], cwd=root, env=environment, check=True)

            candidate = json.loads((runner_temp / "queue-candidate/candidate.json").read_bytes())
            self.assertEqual(candidate["base_sha"], base_sha)
            self.assertEqual(candidate["branch_name"], "claude-issue-135-test")
            self.assertEqual([item["path"] for item in candidate["files"]], ["new.bin"])
            item = candidate["files"][0]
            self.assertFalse(item["deleted"])
            self.assertEqual(item["mode"], "100644")
            self.assertEqual(base64.b64decode(item["content_base64"], validate=True), payload)

    def test_queue_publication_uses_typed_json_and_base_tree(self):
        script = self._queue_python_step("Publish verified bytes through Git Data API without candidate execution")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner_temp = root / "runner"
            artifact = runner_temp / "queue-candidate"
            artifact.mkdir(parents=True)
            base_sha = "a" * 40
            base_tree_sha = "b" * 40
            branch = "claude-issue-135-test"
            added = b"added bytes\n"
            candidate = {
                "version": 1,
                "base_sha": base_sha,
                "branch_name": branch,
                "files": [
                    {
                        "path": "added.txt",
                        "mode": "100644",
                        "deleted": False,
                        "sha256": __import__("hashlib").sha256(added).hexdigest(),
                        "content_base64": base64.b64encode(added).decode(),
                    },
                    {
                        "path": "removed.txt",
                        "mode": None,
                        "deleted": True,
                        "sha256": __import__("hashlib").sha256(b"").hexdigest(),
                        "content_base64": "",
                    },
                ],
            }
            raw = json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode()
            (artifact / "candidate.json").write_bytes(raw)

            bin_dir = root / "bin"
            bin_dir.mkdir()
            log = root / "gh-log.jsonl"
            fake_gh = bin_dir / "gh"
            fake_gh.write_text(
                textwrap.dedent(
                    f"""\
                    #!{sys.executable}
                    import json
                    import os
                    import sys

                    args = sys.argv[1:]
                    path = args[1]
                    method = args[args.index("--method") + 1]
                    payload = json.load(sys.stdin) if "--input" in args else None
                    with open(os.environ["GH_TEST_LOG"], "a", encoding="utf-8") as handle:
                        handle.write(json.dumps({{"path": path, "method": method, "payload": payload, "input": "--input" in args}}) + "\\n")
                    base = "{base_sha}"
                    tree = "{base_tree_sha}"
                    commit = "d" * 40
                    if path.endswith("/git/ref/heads/main"):
                        result = {{"object": {{"sha": base}}}}
                    elif path.endswith("/git/commits/" + base):
                        result = {{"tree": {{"sha": tree}}}}
                    elif path.endswith("/git/blobs"):
                        result = {{"sha": "c" * 40}}
                    elif path.endswith("/git/trees"):
                        result = {{"sha": "e" * 40}}
                    elif path.endswith("/git/commits") and method == "POST":
                        result = {{"sha": commit}}
                    elif path.endswith("/git/ref/heads/{branch}"):
                        raise SystemExit(1)
                    elif path.endswith("/git/refs"):
                        result = {{"object": {{"sha": commit}}}}
                    elif "/pulls?state=open&head=" in path:
                        result = []
                    elif path.endswith("/pulls") and method == "POST":
                        result = {{"state": "open", "draft": True, "base": {{"ref": "main"}}, "head": {{"sha": commit}}, "html_url": "https://example.invalid/pr/1"}}
                    else:
                        raise SystemExit("unexpected gh api path: " + path)
                    print(json.dumps(result))
                    """
                ),
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{bin_dir}{os.pathsep}{environment.get('PATH', '')}",
                    "GH_TEST_LOG": str(log),
                    "GITHUB_REPOSITORY": "example/foundation",
                    "ISSUE_NUMBER": "135",
                    "BASE_REF": "main",
                    "BASE_SHA": base_sha,
                    "BASE_PR": "",
                    "RECOVERY": "false",
                    "BRANCH": branch,
                    "EXPECTED_DIGEST": __import__("hashlib").sha256(raw).hexdigest(),
                    "RUNNER_TEMP": str(runner_temp),
                    "GITHUB_OUTPUT": str(root / "github-output"),
                }
            )
            subprocess.run([sys.executable, "-c", script], cwd=root, env=environment, check=True)

            requests = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
            tree_request = next(item for item in requests if item["path"].endswith("/git/trees"))
            self.assertTrue(tree_request["input"])
            self.assertEqual(tree_request["payload"]["base_tree"], base_tree_sha)
            self.assertIsInstance(tree_request["payload"]["tree"], list)
            deletion = next(item for item in tree_request["payload"]["tree"] if item["path"] == "removed.txt")
            self.assertIsNone(deletion["sha"])

            commit_request = next(
                item for item in requests if item["path"].endswith("/git/commits") and item["method"] == "POST"
            )
            self.assertEqual(commit_request["payload"]["parents"], [base_sha])
            self.assertIsInstance(commit_request["payload"]["parents"], list)

            pull_request = next(
                item for item in requests if item["path"].endswith("/pulls") and item["method"] == "POST"
            )
            self.assertIs(pull_request["payload"]["draft"], True)

    def test_queue_failure_is_non_notifying_and_recovery_is_separated_from_merge_supervisor(self):
        queue = self._queue_workflow()
        finalize = queue.split("\n  finalize:\n", 1)[1]
        self.assertNotIn("QUEUE_PIPELINE_FAILED", finalize)
        self.assertNotIn("gh issue comment", finalize)
        self.assertNotIn("--add-label ai-blocked", finalize)
        self.assertIn("notification: false", finalize)
        self.assertIn("GITHUB_STEP_SUMMARY", finalize)

        reconcile = (ROOT / ".github/workflows/ci-reconcile.yml").read_text(encoding="utf-8")
        queue_recovery = reconcile.split("\n  queue_recovery:\n", 1)[1]
        self.assertIn('workflows: ["CI", "Unit Tests", "Claude Issue Queue"]', reconcile)
        self.assertIn("python -m scripts.supervisor_queue_recovery_v3", queue_recovery)
        self.assertIn("actions: write", queue_recovery)
        self.assertIn("contents: write", queue_recovery)
        self.assertIn("issues: read", queue_recovery)
        self.assertIn("pull-requests: read", queue_recovery)

        supervisor = (ROOT / ".github/workflows/supervisor.yml").read_text(encoding="utf-8")
        self.assertIn('"Claude Issue Queue"', supervisor)
        self.assertIn("python -m scripts.supervisor_final_guard", supervisor)
        self.assertIn("actions: read", supervisor)
        self.assertNotIn("actions: write", supervisor)
        self.assertNotIn("python -m scripts.supervisor_queue_recovery_v3", supervisor)

    def test_merge_capable_guard_is_a_protected_path(self):
        self.assertTrue(is_protected("scripts/supervisor_final_guard.py"))

    def _load_guard(self):
        environment = {
            "REPOSITORY": "example/foundation-e2e",
            "DEFAULT_BRANCH": "main",
            "AUTOMATION_OWNER": "owner",
        }
        with patch.dict(os.environ, environment, clear=False):
            sys.modules.pop("scripts.supervisor_final_guard", None)
            sys.modules.pop("scripts.supervisor_runtime", None)
            return importlib.import_module("scripts.supervisor_final_guard")

    def _trusted_live_pr(self, number: int, *, labels=None, head_sha=CANDIDATE_SHA, state="open", draft=False):
        return {
            "number": number,
            "state": state,
            "draft": draft,
            "mergeable": True,
            "head": {"sha": head_sha, "ref": "fix/candidate", "repo": {"full_name": "example/foundation-e2e"}},
            "base": {"ref": "main", "repo": {"full_name": "example/foundation-e2e"}},
            "user": {"login": "owner"},
            "labels": list(labels or []),
        }

    @staticmethod
    def _scope_result(issue_number=ISSUE_NUMBER, error=None):
        issue = {"number": issue_number, "user": {"login": "owner"}}
        return issue_number, issue, ["scripts/supervisor_final_guard.py"], error

    def test_guard_fails_closed_without_current_successful_attestation(self):
        guard = self._load_guard()
        native = Mock(return_value=(True, ["native-run"]))
        with patch.object(guard, "_original_current_default_sha", return_value=DEFAULT_SHA), patch.object(
            guard.runtime, "attestation_attempts", return_value=[{"success": False}]
        ), patch.object(guard, "_native_workflow_evidence", native):
            self.assertEqual(guard.guarded_native_workflow_evidence(CANDIDATE_SHA, 12), (False, []))
        native.assert_not_called()
        self.assertIsNone(guard._verified_gate)

    def test_guard_binds_attestation_native_and_source_to_same_default_sha(self):
        guard = self._load_guard()
        observed = []

        def attempts(_sha):
            observed.append(("attestation", guard.runtime.current_default_sha()))
            return [{"success": True}]

        def native(_sha, _pr_number):
            observed.append(("native", guard.runtime.current_default_sha()))
            return True, ["native-run"]

        live_pr = self._trusted_live_pr(13)
        with patch.object(guard, "_original_current_default_sha", return_value=DEFAULT_SHA), patch.object(
            guard.runtime, "attestation_attempts", side_effect=attempts
        ), patch.object(guard, "_native_workflow_evidence", side_effect=native), patch.object(
            guard.runtime, "api", return_value=live_pr
        ), patch.object(guard.runtime, "source_and_scope", return_value=self._scope_result()) as scope:
            self.assertEqual(guard.guarded_native_workflow_evidence(CANDIDATE_SHA, 13), (True, ["native-run"]))
        self.assertEqual(observed, [("attestation", DEFAULT_SHA), ("native", DEFAULT_SHA)])
        scope.assert_called_once_with(live_pr)
        self.assertEqual(guard._verified_gate, (CANDIDATE_SHA, 13, DEFAULT_SHA, ISSUE_NUMBER))

    def test_guard_rejects_default_movement_during_native_gate(self):
        guard = self._load_guard()

        def attempts(_sha):
            guard.runtime.current_default_sha()
            return [{"success": True}]

        def native(_sha, _pr_number):
            guard.runtime.current_default_sha()
            return True, ["native-run"]

        with patch.object(guard, "_original_current_default_sha", side_effect=[DEFAULT_SHA, DEFAULT_SHA, "e" * 40]), patch.object(
            guard.runtime, "attestation_attempts", side_effect=attempts
        ), patch.object(guard, "_native_workflow_evidence", side_effect=native):
            self.assertEqual(guard.guarded_native_workflow_evidence(CANDIDATE_SHA, 14), (False, []))
        self.assertIsNone(guard._verified_gate)

    def test_guard_rejects_source_authorization_before_storing_gate(self):
        guard = self._load_guard()
        live_pr = self._trusted_live_pr(14)
        with patch.object(guard, "_original_current_default_sha", return_value=DEFAULT_SHA), patch.object(
            guard.runtime, "attestation_attempts", return_value=[{"success": True}]
        ), patch.object(guard, "_native_workflow_evidence", return_value=(True, ["native-run"])), patch.object(
            guard.runtime, "api", return_value=live_pr
        ), patch.object(
            guard.runtime, "source_and_scope", return_value=self._scope_result(error="UNAUTHORIZED_PROTECTED_PATH")
        ):
            self.assertEqual(guard.guarded_native_workflow_evidence(CANDIDATE_SHA, 14), (False, []))
        self.assertIsNone(guard._verified_gate)

    def test_guard_rejects_missing_or_null_label_evidence_before_storing_gate(self):
        for labels_marker in ("missing", None):
            with self.subTest(labels=labels_marker):
                guard = self._load_guard()
                live_pr = self._trusted_live_pr(14)
                if labels_marker == "missing":
                    live_pr.pop("labels")
                else:
                    live_pr["labels"] = None
                with patch.object(guard, "_original_current_default_sha", return_value=DEFAULT_SHA), patch.object(
                    guard.runtime, "attestation_attempts", return_value=[{"success": True}]
                ), patch.object(guard, "_native_workflow_evidence", return_value=(True, ["native-run"])), patch.object(
                    guard.runtime, "api", return_value=live_pr
                ):
                    self.assertEqual(guard.guarded_native_workflow_evidence(CANDIDATE_SHA, 14), (False, []))
                self.assertIsNone(guard._verified_gate)

    def test_merge_guard_requires_matching_candidate_pr_default_and_source(self):
        guard = self._load_guard()
        delegated = Mock(return_value="merged")
        guard._verified_gate = (CANDIDATE_SHA, 15, DEFAULT_SHA, ISSUE_NUMBER)
        args = ("api", "--method", "PUT", "repos/example/foundation-e2e/pulls/15/merge", "-f", "merge_method=squash", "-f", f"sha={CANDIDATE_SHA}")
        live_pr = self._trusted_live_pr(15)
        with patch.object(guard, "_original_current_default_sha", return_value=DEFAULT_SHA), patch.object(
            guard.runtime, "api", return_value=live_pr
        ) as live, patch.object(guard.runtime, "source_and_scope", return_value=self._scope_result()) as scope, patch.object(
            guard, "_original_gh", delegated
        ):
            self.assertEqual(guard.guarded_gh(*args), "merged")
        live.assert_called_once_with("repos/example/foundation-e2e/pulls/15")
        scope.assert_called_once_with(live_pr)
        delegated.assert_called_once_with(*args)
        self.assertIsNone(guard._verified_gate)

    def test_failed_merge_attempt_consumes_gate(self):
        guard = self._load_guard()
        guard._verified_gate = (CANDIDATE_SHA, 20, DEFAULT_SHA, ISSUE_NUMBER)
        args = ("api", "--method", "PUT", "repos/example/foundation-e2e/pulls/21/merge", "-f", f"sha={CANDIDATE_SHA}")
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            guard.guarded_gh(*args)
        self.assertIsNone(guard._verified_gate)
        with self.assertRaisesRegex(RuntimeError, "no verified"):
            guard.guarded_gh(*args)

    def test_merge_guard_rejects_mismatch_and_final_default_movement(self):
        guard = self._load_guard()
        delegated = Mock(return_value="merged")
        args = ("api", "--method", "PUT", "repos/example/foundation-e2e/pulls/16/merge", "-f", f"sha={CANDIDATE_SHA}")
        guard._verified_gate = (CANDIDATE_SHA, 15, DEFAULT_SHA, ISSUE_NUMBER)
        with patch.object(guard, "_original_gh", delegated):
            with self.assertRaisesRegex(RuntimeError, "does not match"):
                guard.guarded_gh(*args)
        self.assertIsNone(guard._verified_gate)
        delegated.assert_not_called()
        guard._verified_gate = (CANDIDATE_SHA, 16, DEFAULT_SHA, ISSUE_NUMBER)
        with patch.object(guard, "_original_current_default_sha", return_value="e" * 40), patch.object(
            guard.runtime, "api", return_value=self._trusted_live_pr(16)
        ), patch.object(guard.runtime, "source_and_scope", return_value=self._scope_result()), patch.object(
            guard, "_original_gh", delegated
        ):
            with self.assertRaisesRegex(RuntimeError, "moved"):
                guard.guarded_gh(*args)
        self.assertIsNone(guard._verified_gate)
        delegated.assert_not_called()

    def test_merge_guard_rejects_live_ai_no_merge_or_head_movement(self):
        guard = self._load_guard()
        delegated = Mock(return_value="merged")
        args = ("api", "--method", "PUT", "repos/example/foundation-e2e/pulls/17/merge", "-f", f"sha={CANDIDATE_SHA}")
        guard._verified_gate = (CANDIDATE_SHA, 17, DEFAULT_SHA, ISSUE_NUMBER)
        blocked = self._trusted_live_pr(17, labels=[{"name": "ai-no-merge"}])
        with patch.object(guard.runtime, "api", return_value=blocked), patch.object(guard, "_original_gh", delegated):
            with self.assertRaisesRegex(RuntimeError, "trusted candidate"):
                guard.guarded_gh(*args)
        self.assertIsNone(guard._verified_gate)
        delegated.assert_not_called()
        guard._verified_gate = (CANDIDATE_SHA, 17, DEFAULT_SHA, ISSUE_NUMBER)
        moved = self._trusted_live_pr(17, head_sha="e" * 40)
        with patch.object(guard.runtime, "api", return_value=moved), patch.object(guard, "_original_gh", delegated):
            with self.assertRaisesRegex(RuntimeError, "trusted candidate"):
                guard.guarded_gh(*args)
        self.assertIsNone(guard._verified_gate)
        delegated.assert_not_called()

    def test_merge_guard_rejects_incomplete_closed_draft_or_label_evidence(self):
        guard = self._load_guard()
        delegated = Mock(return_value="merged")
        args = ("api", "--method", "PUT", "repos/example/foundation-e2e/pulls/18/merge", "-f", f"sha={CANDIDATE_SHA}")
        missing_labels = self._trusted_live_pr(18)
        missing_labels.pop("labels")
        null_labels = self._trusted_live_pr(18)
        null_labels["labels"] = None
        for live in (
            self._trusted_live_pr(18, state="closed"),
            self._trusted_live_pr(18, draft=True),
            self._trusted_live_pr(18, draft=None),
            missing_labels,
            null_labels,
        ):
            with self.subTest(state=live["state"], draft=live.get("draft"), labels=live.get("labels", "missing")):
                guard._verified_gate = (CANDIDATE_SHA, 18, DEFAULT_SHA, ISSUE_NUMBER)
                with patch.object(guard.runtime, "api", return_value=live), patch.object(guard, "_original_gh", delegated):
                    with self.assertRaises(RuntimeError):
                        guard.guarded_gh(*args)
                self.assertIsNone(guard._verified_gate)
        delegated.assert_not_called()

    def test_merge_guard_rejects_source_issue_or_authorization_movement(self):
        guard = self._load_guard()
        delegated = Mock(return_value="merged")
        args = ("api", "--method", "PUT", "repos/example/foundation-e2e/pulls/19/merge", "-f", f"sha={CANDIDATE_SHA}")
        live_pr = self._trusted_live_pr(19)
        guard._verified_gate = (CANDIDATE_SHA, 19, DEFAULT_SHA, ISSUE_NUMBER)
        with patch.object(guard.runtime, "api", return_value=live_pr), patch.object(
            guard.runtime, "source_and_scope", return_value=self._scope_result(error="UNAUTHORIZED_CHANGED_PATH")
        ), patch.object(guard, "_original_gh", delegated):
            with self.assertRaisesRegex(RuntimeError, "authorization"):
                guard.guarded_gh(*args)
        self.assertIsNone(guard._verified_gate)
        delegated.assert_not_called()
        guard._verified_gate = (CANDIDATE_SHA, 19, DEFAULT_SHA, ISSUE_NUMBER)
        with patch.object(guard.runtime, "api", return_value=live_pr), patch.object(
            guard.runtime, "source_and_scope", return_value=self._scope_result(86)
        ), patch.object(guard, "_original_gh", delegated):
            with self.assertRaisesRegex(RuntimeError, "source Issue"):
                guard.guarded_gh(*args)
        self.assertIsNone(guard._verified_gate)
        delegated.assert_not_called()

    def test_unrelated_gh_calls_pass_through(self):
        guard = self._load_guard()
        delegated = Mock(return_value="ok")
        with patch.object(guard, "_original_gh", delegated):
            self.assertEqual(guard.guarded_gh("api", "repos/example/foundation-e2e"), "ok")
        delegated.assert_called_once_with("api", "repos/example/foundation-e2e")

    def test_exact_head_codex_request_delegates_to_provider_dispatch(self):
        guard = self._load_guard()
        delegated = Mock()
        with patch.object(guard, "_original_request_codex", delegated):
            guard.request_codex_exact_head(22, CANDIDATE_SHA)
        delegated.assert_called_once_with(22, CANDIDATE_SHA)
        with self.assertRaises(ValueError):
            guard.request_codex_exact_head(22, "not-a-sha")

    def test_main_installs_all_final_guards_before_runtime(self):
        guard = self._load_guard()
        delegated = Mock(return_value=0)
        with patch.object(guard.runtime, "main", delegated):
            self.assertEqual(guard.main(), 0)
        self.assertIs(guard.runtime.native_workflow_evidence, guard.guarded_native_workflow_evidence)
        self.assertIs(guard.runtime.gh, guard.guarded_gh)
        self.assertIs(guard.runtime.request_codex, guard.request_codex_exact_head)
        delegated.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

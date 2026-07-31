#!/usr/bin/env python3
"""Apply the fixed immutable final mutation-race repair to PR #44."""
from __future__ import annotations

import base64
import json
import os
import subprocess

REPOSITORY = os.environ["GITHUB_REPOSITORY"]
TARGET_PR = int(os.environ["TARGET_PR"])
TARGET_BRANCH = os.environ["TARGET_BRANCH"]
EXPECTED_HEAD = os.environ["EXPECTED_HEAD"]
TARGET_PATHS = (
    "scripts/supervisor_runtime.py",
    "tests/test_runtime_human_notice.py",
    "tests/test_runtime_scope_and_checks.py",
)

REVALIDATE_OLD = '''def _revalidate_stop_reason(
    pr_number: int,
    sha: str,
    issue_number: int | None,
    reason: str,
) -> dict[str, Any]:
    live = _live_pr(pr_number, sha)
    if reason == "MERGE_NOT_READY" and live.get("mergeable") is not False:
        raise RuntimeError("terminal mergeability changed before stop mutation")
    if reason in {"UNAUTHORIZED_CHANGED_PATH", "UNAUTHORIZED_PROTECTED_PATH"}:
        fresh_issue_number, _, _, fresh_reason = source_and_scope(live)
        if fresh_issue_number != issue_number or fresh_reason != reason:
            raise RuntimeError(
                f"{reason} is no longer supported immediately before stop mutation"
            )
    return live
'''

REVALIDATE_NEW = '''def _revalidate_stop_reason(
    pr_number: int,
    sha: str,
    issue_number: int | None,
    reason: str,
) -> dict[str, Any]:
    live = _live_pr(pr_number, sha)
    fresh_issue_number, _, _, fresh_scope_reason = source_and_scope(live)
    scope_reasons = {
        "MISSING_TRUSTED_SOURCE_ISSUE",
        "UNTRUSTED_SOURCE_ISSUE",
        "INCOMPLETE_CHANGED_FILE_EVIDENCE",
        "UNAUTHORIZED_CHANGED_PATH",
        "UNAUTHORIZED_PROTECTED_PATH",
    }
    if reason in scope_reasons:
        if fresh_issue_number != issue_number or fresh_scope_reason != reason:
            raise RuntimeError(
                f"{reason} is no longer supported immediately before stop mutation"
            )
        return _live_pr(pr_number, sha)
    if fresh_scope_reason is not None:
        raise RuntimeError(
            f"{reason} is no longer supported because source/scope evidence changed"
        )

    attempts = attestation_attempts(sha)
    successful_attestation = any(item["success"] for item in attempts)
    active_attestation = any(item["active"] for item in attempts)
    attempt_count = len({item["run_id"] for item in attempts})

    if reason == "TRUSTED_ATTESTATION_RETRY_EXHAUSTED":
        if successful_attestation or active_attestation or attempt_count < MAX_ATTESTATION_ATTEMPTS:
            raise RuntimeError(
                "TRUSTED_ATTESTATION_RETRY_EXHAUSTED is no longer supported by fresh attestation evidence"
            )
        return _live_pr(pr_number, sha)

    if not successful_attestation:
        raise RuntimeError(
            f"{reason} is no longer supported without a fresh successful trusted attestation"
        )

    native_clean, native_evidence = native_workflow_evidence(sha, pr_number)
    if reason == "NO_MEANINGFUL_PROGRESS":
        supported = False
        if not native_clean:
            anchor = max(
                (
                    str(item.get("updated_at") or "")
                    for item in native_evidence
                    if item.get("updated_at")
                ),
                default=None,
            )
            elapsed = minutes_since(anchor)
            supported = elapsed is not None and elapsed >= NO_PROGRESS_MINUTES
        else:
            codex = exact_codex_evidence(pr_number, sha)
            if codex["state"] == "pending":
                elapsed = minutes_since(codex.get("request_timestamp"))
                supported = elapsed is not None and elapsed >= NO_PROGRESS_MINUTES
            elif codex["state"] == "clean":
                final_live = _live_pr(pr_number, sha)
                if final_live.get("mergeable") not in {True, False}:
                    anchor = _evidence_anchor(
                        latest_successful_attestation_timestamp(attempts),
                        str(codex.get("timestamp") or "") or None,
                        *(
                            str(item.get("updated_at") or "") or None
                            for item in native_evidence
                        ),
                    )
                    elapsed = minutes_since(anchor)
                    supported = elapsed is not None and elapsed >= NO_PROGRESS_MINUTES
        if not supported:
            raise RuntimeError(
                "NO_MEANINGFUL_PROGRESS is no longer supported by fresh exact-head evidence"
            )
        return _live_pr(pr_number, sha)

    if reason == "BLOCKING_CODEX_REVIEW":
        codex = exact_codex_evidence(pr_number, sha)
        if not native_clean or codex["state"] != "blocking":
            raise RuntimeError(
                "BLOCKING_CODEX_REVIEW is no longer supported by fresh exact-head evidence"
            )
        return _live_pr(pr_number, sha)

    if reason == "MERGE_NOT_READY":
        codex = exact_codex_evidence(pr_number, sha)
        final_live = _live_pr(pr_number, sha)
        if not native_clean or codex["state"] != "clean" or final_live.get("mergeable") is not False:
            raise RuntimeError(
                "MERGE_NOT_READY is no longer supported by fresh exact-head evidence"
            )
        return final_live

    if reason in {"UNTRUSTED_EVIDENCE", "AMBIGUOUS_TECHNICAL_STATE"}:
        raise RuntimeError(
            f"{reason} has no deterministic current derivation and fails closed"
        )
    raise RuntimeError(f"Unsupported internal stop reason: {reason}")
'''

NOTICE_TAIL_OLD = '''    final_attempted, final_impossible = _connected_human_notice_evidence(
        reason, target_list
    )
    if final_attempted != connected_attempted or final_impossible != connected_impossible:
        raise RuntimeError("Connected human-only condition changed after the final audit")
    comments = api_list(f"repos/{REPO}/issues/{pr_number}/comments?per_page=100")
    trusted_duplicate = any(
        (item.get("user") or {}).get("login") == ACTIONS_LOGIN
        and item.get("created_at") == item.get("updated_at")
        and marker in (item.get("body") or "")
        for item in comments
    )
    if trusted_duplicate:
        if _existing_internal_record(record_path) != record:
            raise RuntimeError("trusted notice comment has no matching persisted exact audit record")
        return
    _validated_notice_destination(pr_number, issue_number, exact_head_sha)
    final_attempted, final_impossible = _connected_human_notice_evidence(
        reason, target_list
    )
    if final_attempted != connected_attempted or final_impossible != connected_impossible:
        raise RuntimeError("Connected human-only condition changed before publication")
    if _existing_internal_record(record_path) != record:
        raise RuntimeError("human-only audit record changed before publication")
    comment(pr_number, body)
'''

NOTICE_TAIL_NEW = '''    final_attempted, final_impossible = _connected_human_notice_evidence(
        reason, target_list
    )
    if final_attempted != connected_attempted or final_impossible != connected_impossible:
        raise RuntimeError("Connected human-only condition changed after the final audit")
    comments = api_list(f"repos/{REPO}/issues/{pr_number}/comments?per_page=100")
    trusted_duplicate = any(
        (item.get("user") or {}).get("login") == ACTIONS_LOGIN
        and item.get("created_at") == item.get("updated_at")
        and marker in (item.get("body") or "")
        for item in comments
    )
    persisted_record = _existing_internal_record(record_path)
    if trusted_duplicate:
        if persisted_record != record:
            raise RuntimeError("trusted notice comment has no matching persisted exact audit record")
        return
    final_attempted, final_impossible = _connected_human_notice_evidence(
        reason, target_list
    )
    if final_attempted != connected_attempted or final_impossible != connected_impossible:
        raise RuntimeError("Connected human-only condition changed before publication")
    if persisted_record != record:
        raise RuntimeError("human-only audit record changed before publication")
    _validated_notice_destination(pr_number, issue_number, exact_head_sha)
    comment(pr_number, body)
'''

MERGE_TAIL_OLD = '''        final = _live_pr(pr_number, sha)
        if final.get("mergeable") is not True or not trusted_candidate(final):
            continue
        final_native_clean, _ = native_workflow_evidence(sha, pr_number)
        if not final_native_clean or not exact_codex_clean(pr_number, sha):
            continue

        merge_candidate = _live_pr(pr_number, sha)
        if merge_candidate.get("mergeable") is not True or not trusted_candidate(
            merge_candidate
        ):
            continue
        final_issue_number, _, _, final_scope_error = source_and_scope(merge_candidate)
        if final_issue_number != issue_number or final_scope_error:
            continue
        if not exact_codex_clean(pr_number, sha):
            continue
        merge_candidate = _live_pr(pr_number, sha)
        if (
            merge_candidate.get("mergeable") is not True
            or not trusted_candidate(merge_candidate)
            or parse_issue_number(merge_candidate.get("body") or "") != issue_number
        ):
            continue
        if not exact_codex_clean(pr_number, sha):
            continue
        merge_candidate = _live_pr(pr_number, sha)
        if (
            merge_candidate.get("mergeable") is not True
            or not trusted_candidate(merge_candidate)
            or parse_issue_number(merge_candidate.get("body") or "") != issue_number
        ):
            continue
        gh(
'''

MERGE_TAIL_NEW = '''        final = _live_pr(pr_number, sha)
        if final.get("mergeable") is not True or not trusted_candidate(final):
            continue
        final_native_clean, _ = native_workflow_evidence(sha, pr_number)
        if not final_native_clean or not exact_codex_clean(pr_number, sha):
            continue

        scope_candidate = _live_pr(pr_number, sha)
        final_issue_number, _, _, final_scope_error = source_and_scope(scope_candidate)
        if final_issue_number != issue_number or final_scope_error:
            continue
        merge_candidate = _live_pr(pr_number, sha)
        if (
            merge_candidate.get("mergeable") is not True
            or not trusted_candidate(merge_candidate)
            or parse_issue_number(merge_candidate.get("body") or "") != issue_number
        ):
            continue
        gh(
'''

HUMAN_STOP_TEST_OLD = '''        with (
            patch.object(self.runtime, "self_resolution_audit", return_value={"a": "1"}),
            patch.object(self.runtime, "_live_pr", return_value=self.pr),
            patch.object(
                self.runtime, "persist_internal_stop_record", return_value=True
            ) as persist,
'''
HUMAN_STOP_TEST_NEW = '''        with (
            patch.object(self.runtime, "self_resolution_audit", return_value={"a": "1"}),
            patch.object(
                self.runtime, "_revalidate_stop_reason", return_value=self.pr
            ),
            patch.object(
                self.runtime, "persist_internal_stop_record", return_value=True
            ) as persist,
'''

HUMAN_TEST_MARKER = '\n\nif __name__ == "__main__":\n    unittest.main()\n'
HUMAN_TEST_INSERT = r'''

    def test_notice_destination_is_last_network_validation_before_comment(self):
        fields = self.valid_fields()
        live = self.notice_pr()
        records = {}
        events = []

        def connected(reason, targets):
            events.append("connected")
            return self.attempted, self.impossible

        def persist(path, content, reason, number):
            records[path] = content
            return True

        def existing(path):
            events.append("record")
            return records.get(path)

        def destination(*args):
            events.append("destination")
            return live

        with (
            patch.object(
                self.runtime,
                "_connected_human_notice_evidence",
                side_effect=connected,
            ),
            patch.object(
                self.runtime,
                "_validated_notice_destination",
                side_effect=destination,
            ),
            patch.object(
                self.runtime,
                "self_resolution_audit",
                return_value={"human_only_connected_evidence": "bound"},
            ),
            patch.object(
                self.runtime,
                "api_list",
                side_effect=lambda path: events.append("comments") or [],
            ),
            patch.object(
                self.runtime,
                "persist_human_notice_record",
                side_effect=persist,
            ),
            patch.object(
                self.runtime,
                "_existing_internal_record",
                side_effect=existing,
            ),
            patch.object(
                self.runtime,
                "comment",
                side_effect=lambda number, body: events.append("comment"),
            ),
        ):
            self.runtime.human_only_notice(**fields)

        self.assertEqual(events[-2:], ["destination", "comment"])
        final_destination = len(events) - 2
        self.assertNotIn("connected", events[final_destination + 1 : -1])
        self.assertNotIn("record", events[final_destination + 1 : -1])
'''

SCOPE_CLASS_MARKER = '\n\nclass FinalMergeGateRevalidationTest(unittest.TestCase):\n'
SCOPE_TEST_INSERT = r'''

class CompleteStopReasonRevalidationTest(unittest.TestCase):
    def setUp(self):
        self.runtime = load_runtime()
        self.candidate = {
            "number": PR_NUMBER,
            "state": "open",
            "head": {"sha": SHA},
            "mergeable": True,
            "mergeable_state": "clean",
        }
        self.clean_scope = (9, {"number": 9}, ["docs/probe.md"], None)
        self.successful_attempt = [
            {
                "success": True,
                "active": False,
                "run_id": 1,
                "updated_at": "2026-07-31T12:00:00Z",
            }
        ]

    def test_each_source_scope_reason_must_still_match(self):
        reasons = (
            "MISSING_TRUSTED_SOURCE_ISSUE",
            "UNTRUSTED_SOURCE_ISSUE",
            "INCOMPLETE_CHANGED_FILE_EVIDENCE",
            "UNAUTHORIZED_CHANGED_PATH",
            "UNAUTHORIZED_PROTECTED_PATH",
        )
        for reason in reasons:
            with (
                self.subTest(reason=reason),
                patch.object(self.runtime, "_live_pr", return_value=self.candidate),
                patch.object(
                    self.runtime,
                    "source_and_scope",
                    return_value=self.clean_scope,
                ),
                self.assertRaisesRegex(RuntimeError, "no longer supported"),
            ):
                self.runtime._revalidate_stop_reason(PR_NUMBER, SHA, 9, reason)

    def test_retry_exhaustion_cleared_by_success_fails_closed(self):
        with (
            patch.object(self.runtime, "_live_pr", return_value=self.candidate),
            patch.object(self.runtime, "source_and_scope", return_value=self.clean_scope),
            patch.object(
                self.runtime,
                "attestation_attempts",
                return_value=self.successful_attempt,
            ),
            self.assertRaisesRegex(RuntimeError, "no longer supported"),
        ):
            self.runtime._revalidate_stop_reason(
                PR_NUMBER, SHA, 9, "TRUSTED_ATTESTATION_RETRY_EXHAUSTED"
            )

    def test_current_retry_exhaustion_remains_supported(self):
        exhausted = [
            {"success": False, "active": False, "run_id": number}
            for number in (1, 2, 3)
        ]
        with (
            patch.object(self.runtime, "_live_pr", return_value=self.candidate),
            patch.object(self.runtime, "source_and_scope", return_value=self.clean_scope),
            patch.object(self.runtime, "attestation_attempts", return_value=exhausted),
        ):
            live = self.runtime._revalidate_stop_reason(
                PR_NUMBER, SHA, 9, "TRUSTED_ATTESTATION_RETRY_EXHAUSTED"
            )
        self.assertIs(live, self.candidate)

    def test_no_progress_cleared_by_fresh_native_evidence_fails_closed(self):
        native = [
            {
                "updated_at": "2026-07-31T12:00:00Z",
                "status": "in_progress",
                "conclusion": None,
            }
        ]
        with (
            patch.object(self.runtime, "_live_pr", return_value=self.candidate),
            patch.object(self.runtime, "source_and_scope", return_value=self.clean_scope),
            patch.object(
                self.runtime,
                "attestation_attempts",
                return_value=self.successful_attempt,
            ),
            patch.object(
                self.runtime,
                "native_workflow_evidence",
                return_value=(False, native),
            ),
            patch.object(self.runtime, "minutes_since", return_value=1),
            self.assertRaisesRegex(RuntimeError, "no longer supported"),
        ):
            self.runtime._revalidate_stop_reason(
                PR_NUMBER, SHA, 9, "NO_MEANINGFUL_PROGRESS"
            )

    def test_blocking_codex_reason_cleared_by_clean_review_fails_closed(self):
        with (
            patch.object(self.runtime, "_live_pr", return_value=self.candidate),
            patch.object(self.runtime, "source_and_scope", return_value=self.clean_scope),
            patch.object(
                self.runtime,
                "attestation_attempts",
                return_value=self.successful_attempt,
            ),
            patch.object(
                self.runtime,
                "native_workflow_evidence",
                return_value=(True, []),
            ),
            patch.object(
                self.runtime,
                "exact_codex_evidence",
                return_value={"state": "clean", "timestamp": None, "request_timestamp": None},
            ),
            self.assertRaisesRegex(RuntimeError, "no longer supported"),
        ):
            self.runtime._revalidate_stop_reason(
                PR_NUMBER, SHA, 9, "BLOCKING_CODEX_REVIEW"
            )

    def test_merge_not_ready_cleared_by_mergeable_candidate_fails_closed(self):
        with (
            patch.object(self.runtime, "_live_pr", return_value=self.candidate),
            patch.object(self.runtime, "source_and_scope", return_value=self.clean_scope),
            patch.object(
                self.runtime,
                "attestation_attempts",
                return_value=self.successful_attempt,
            ),
            patch.object(
                self.runtime,
                "native_workflow_evidence",
                return_value=(True, []),
            ),
            patch.object(
                self.runtime,
                "exact_codex_evidence",
                return_value={"state": "clean", "timestamp": None, "request_timestamp": None},
            ),
            self.assertRaisesRegex(RuntimeError, "no longer supported"),
        ):
            self.runtime._revalidate_stop_reason(PR_NUMBER, SHA, 9, "MERGE_NOT_READY")

    def test_reasons_without_current_derivation_fail_closed(self):
        for reason in ("UNTRUSTED_EVIDENCE", "AMBIGUOUS_TECHNICAL_STATE"):
            with (
                self.subTest(reason=reason),
                patch.object(self.runtime, "_live_pr", return_value=self.candidate),
                patch.object(
                    self.runtime,
                    "source_and_scope",
                    return_value=self.clean_scope,
                ),
                patch.object(
                    self.runtime,
                    "attestation_attempts",
                    return_value=self.successful_attempt,
                ),
                patch.object(
                    self.runtime,
                    "native_workflow_evidence",
                    return_value=(True, []),
                ),
                self.assertRaisesRegex(RuntimeError, "fails closed"),
            ):
                self.runtime._revalidate_stop_reason(PR_NUMBER, SHA, 9, reason)
'''

SCOPE_FINAL_TEST_MARKER = '\n\nif __name__ == "__main__":\n    unittest.main()\n'
SCOPE_FINAL_TEST_INSERT = r'''

class FinalMergeOrderingSourceTest(unittest.TestCase):
    def test_source_scope_runs_after_last_codex_query_and_before_last_live_snapshot(self):
        runtime = open(load_runtime().__file__, encoding="utf-8").read()
        tail = runtime.split("        final = _live_pr(pr_number, sha)", 1)[1]
        tail = tail.split("        gh(\n", 1)[0]
        last_codex = tail.rfind("exact_codex_clean(pr_number, sha)")
        last_scope = tail.rfind("source_and_scope(scope_candidate)")
        last_live = tail.rfind("merge_candidate = _live_pr(pr_number, sha)")
        self.assertGreater(last_codex, -1)
        self.assertGreater(last_scope, last_codex)
        self.assertGreater(last_live, last_scope)
'''


def gh(*args: str, input_value: str | None = None) -> str:
    result = subprocess.run(
        ["gh", *args],
        input=input_value,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "gh command failed: " + " ".join(args) + "\n" + result.stderr.strip()
        )
    return result.stdout


def api(path: str) -> dict:
    return json.loads(gh("api", "-H", "Accept: application/vnd.github+json", path))


def api_json(method: str, path: str, payload: dict) -> dict:
    return json.loads(
        gh(
            "api",
            "--method",
            method,
            "-H",
            "Accept: application/vnd.github+json",
            "--input",
            "-",
            path,
            input_value=json.dumps(payload),
        )
    )


def require_target(snapshot: dict) -> None:
    head = snapshot.get("head") or {}
    base = snapshot.get("base") or {}
    if snapshot.get("state") != "open":
        print("Target Pull Request is no longer open; nothing to repair.")
        raise SystemExit(0)
    if (head.get("repo") or {}).get("full_name") != REPOSITORY:
        raise RuntimeError("Target Pull Request head is not same-repository")
    if (base.get("repo") or {}).get("full_name") != REPOSITORY:
        raise RuntimeError("Target Pull Request base is not same-repository")
    if head.get("ref") != TARGET_BRANCH:
        raise RuntimeError("Target Pull Request branch changed")
    if head.get("sha") != EXPECTED_HEAD:
        print("Target head moved; exact repair is no longer applicable.")
        raise SystemExit(0)


def main() -> None:
    pull = api(f"repos/{REPOSITORY}/pulls/{TARGET_PR}")
    require_target(pull)

    commit = api(f"repos/{REPOSITORY}/git/commits/{EXPECTED_HEAD}")
    tree_sha = (commit.get("tree") or {}).get("sha")
    if not tree_sha:
        raise RuntimeError("Expected commit has no tree")
    tree = api(f"repos/{REPOSITORY}/git/trees/{tree_sha}?recursive=1")
    entries = {
        item.get("path"): item
        for item in tree.get("tree") or []
        if item.get("path") in TARGET_PATHS
    }
    if set(entries) != set(TARGET_PATHS):
        raise RuntimeError("Immutable expected tree does not contain exact target paths")

    texts: dict[str, str] = {}
    for path in TARGET_PATHS:
        entry = entries[path]
        if entry.get("type") != "blob" or entry.get("mode") not in {"100644", "100755"}:
            raise RuntimeError(f"Unexpected immutable file type or mode for {path}")
        metadata = api(f"repos/{REPOSITORY}/contents/{path}?ref={EXPECTED_HEAD}")
        if metadata.get("sha") != entry.get("sha") or metadata.get("encoding") != "base64":
            raise RuntimeError(f"Immutable blob/tree binding failed for {path}")
        encoded = "".join((metadata.get("content") or "").split())
        texts[path] = base64.b64decode(encoded.encode("ascii"), validate=True).decode("utf-8")

    runtime = texts["scripts/supervisor_runtime.py"]
    for old, new, label in (
        (REVALIDATE_OLD, REVALIDATE_NEW, "complete stop revalidation"),
        (NOTICE_TAIL_OLD, NOTICE_TAIL_NEW, "human notice publication ordering"),
        (MERGE_TAIL_OLD, MERGE_TAIL_NEW, "final merge ordering"),
    ):
        if runtime.count(old) != 1:
            raise RuntimeError(f"{label} anchor count is not exactly one")
        runtime = runtime.replace(old, new, 1)
    compile(runtime, "scripts/supervisor_runtime.py", "exec")

    human_tests = texts["tests/test_runtime_human_notice.py"]
    if human_tests.count(HUMAN_STOP_TEST_OLD) != 1:
        raise RuntimeError("human stop-report test anchor count is not exactly one")
    human_tests = human_tests.replace(HUMAN_STOP_TEST_OLD, HUMAN_STOP_TEST_NEW, 1)
    if "test_notice_destination_is_last_network_validation_before_comment" in human_tests:
        raise RuntimeError("human notice ordering test already exists unexpectedly")
    if human_tests.count(HUMAN_TEST_MARKER) != 1:
        raise RuntimeError("human test insertion marker count is not exactly one")
    human_tests = human_tests.replace(
        HUMAN_TEST_MARKER,
        HUMAN_TEST_INSERT + HUMAN_TEST_MARKER,
        1,
    )
    compile(human_tests, "tests/test_runtime_human_notice.py", "exec")

    scope_tests = texts["tests/test_runtime_scope_and_checks.py"]
    if "class CompleteStopReasonRevalidationTest" in scope_tests:
        raise RuntimeError("complete stop-reason tests already exist unexpectedly")
    if scope_tests.count(SCOPE_CLASS_MARKER) != 1:
        raise RuntimeError("scope class insertion marker count is not exactly one")
    scope_tests = scope_tests.replace(
        SCOPE_CLASS_MARKER,
        SCOPE_TEST_INSERT + SCOPE_CLASS_MARKER,
        1,
    )
    if "class FinalMergeOrderingSourceTest" in scope_tests:
        raise RuntimeError("final merge ordering source test already exists unexpectedly")
    if scope_tests.count(SCOPE_FINAL_TEST_MARKER) != 1:
        raise RuntimeError("scope final insertion marker count is not exactly one")
    scope_tests = scope_tests.replace(
        SCOPE_FINAL_TEST_MARKER,
        SCOPE_FINAL_TEST_INSERT + SCOPE_FINAL_TEST_MARKER,
        1,
    )
    compile(scope_tests, "tests/test_runtime_scope_and_checks.py", "exec")

    replacements = {
        "scripts/supervisor_runtime.py": runtime,
        "tests/test_runtime_human_notice.py": human_tests,
        "tests/test_runtime_scope_and_checks.py": scope_tests,
    }
    tree_items = []
    for path in TARGET_PATHS:
        blob = api_json(
            "POST",
            f"repos/{REPOSITORY}/git/blobs",
            {"content": replacements[path], "encoding": "utf-8"},
        )
        tree_items.append(
            {
                "path": path,
                "mode": entries[path]["mode"],
                "type": "blob",
                "sha": blob["sha"],
            }
        )

    new_tree = api_json(
        "POST",
        f"repos/{REPOSITORY}/git/trees",
        {"base_tree": tree_sha, "tree": tree_items},
    )
    new_commit = api_json(
        "POST",
        f"repos/{REPOSITORY}/git/commits",
        {
            "message": "Close final stop and publication mutation races",
            "tree": new_tree["sha"],
            "parents": [EXPECTED_HEAD],
        },
    )

    live = api(f"repos/{REPOSITORY}/pulls/{TARGET_PR}")
    require_target(live)
    api_json(
        "PATCH",
        f"repos/{REPOSITORY}/git/refs/heads/{TARGET_BRANCH}",
        {"sha": new_commit["sha"], "force": False},
    )
    print(f"Repaired PR #{TARGET_PR} from {EXPECTED_HEAD} to {new_commit['sha']}.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Apply the final reviewed inert text repairs to the fixed PR #44 head."""
from __future__ import annotations

import base64
import json
import os
import subprocess

REPOSITORY = os.environ["GITHUB_REPOSITORY"]
PR_NUMBER = 44
TARGET_BRANCH = "fix/issue-36-human-notice-contract"
EXPECTED_HEAD = "67db4b01dde795f4250d7a19a0a400865f1043fe"
SOURCE_ISSUE = 36
RUNTIME_PATH = "scripts/supervisor_runtime.py"
TEST_PATH = "tests/test_runtime_scope_and_checks.py"


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
    if base.get("ref") != "main" or head.get("ref") != TARGET_BRANCH:
        raise RuntimeError("Target Pull Request branch provenance changed")
    if head.get("sha") != EXPECTED_HEAD:
        print("Target head moved; exact repair is no longer applicable.")
        raise SystemExit(0)
    if f"#{SOURCE_ISSUE}" not in (snapshot.get("body") or ""):
        raise RuntimeError("Target Pull Request no longer links the fixed source Issue")


def replace_once(content: str, old: str, new: str, label: str) -> str:
    if content.count(old) != 1:
        raise RuntimeError(f"{label} anchor count is not exactly one")
    return content.replace(old, new, 1)


def repair_runtime(content: str) -> str:
    old_revalidate = '''def _revalidate_stop_reason(
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
    new_revalidate = '''def _no_progress_reason_is_current(
    live: dict[str, Any], pr_number: int, sha: str
) -> bool:
    attempts = attestation_attempts(sha)
    if not any(item["success"] for item in attempts):
        return False

    native_clean, native_evidence = native_workflow_evidence(sha, pr_number)
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
        return elapsed is not None and elapsed >= NO_PROGRESS_MINUTES

    codex = exact_codex_evidence(pr_number, sha)
    if codex["state"] == "pending":
        elapsed = minutes_since(codex.get("request_timestamp"))
        return elapsed is not None and elapsed >= NO_PROGRESS_MINUTES
    if codex["state"] != "clean":
        return False

    if live.get("mergeable") not in {True, False}:
        anchor = _evidence_anchor(
            latest_successful_attestation_timestamp(attempts),
            str(codex.get("timestamp") or "") or None,
            *(
                str(item.get("updated_at") or "") or None
                for item in native_evidence
            ),
        )
        elapsed = minutes_since(anchor)
        return elapsed is not None and elapsed >= NO_PROGRESS_MINUTES
    return False


def _revalidate_stop_reason(
    pr_number: int,
    sha: str,
    issue_number: int | None,
    reason: str,
) -> dict[str, Any]:
    live = _live_pr(pr_number, sha)
    source_reasons = {
        "MISSING_TRUSTED_SOURCE_ISSUE",
        "UNTRUSTED_SOURCE_ISSUE",
        "INCOMPLETE_CHANGED_FILE_EVIDENCE",
        "UNAUTHORIZED_CHANGED_PATH",
        "UNAUTHORIZED_PROTECTED_PATH",
    }
    fresh_issue_number, _, _, fresh_scope_reason = source_and_scope(live)
    if reason in source_reasons:
        if fresh_issue_number != issue_number or fresh_scope_reason != reason:
            raise RuntimeError(
                f"{reason} is no longer supported immediately before stop mutation"
            )
        return live
    if fresh_issue_number != issue_number or fresh_scope_reason is not None:
        raise RuntimeError(
            "trusted source Issue or path authorization changed before stop mutation"
        )

    if reason == "MERGE_NOT_READY":
        current = live.get("mergeable") is False
    elif reason == "BLOCKING_CODEX_REVIEW":
        current = exact_codex_state(pr_number, sha) == "blocking"
    elif reason == "TRUSTED_ATTESTATION_RETRY_EXHAUSTED":
        attempts = attestation_attempts(sha)
        current = bool(
            not any(item["success"] for item in attempts)
            and not any(item["active"] for item in attempts)
            and len({item["run_id"] for item in attempts})
            >= MAX_ATTESTATION_ATTEMPTS
        )
    elif reason == "NO_MEANINGFUL_PROGRESS":
        current = _no_progress_reason_is_current(live, pr_number, sha)
    else:
        raise RuntimeError(
            f"{reason} has no independent live revalidation adapter"
        )
    if not current:
        raise RuntimeError(
            f"{reason} is no longer supported immediately before stop mutation"
        )
    return live
'''
    content = replace_once(content, old_revalidate, new_revalidate, "stop revalidation")

    old_notice = '''    _validated_notice_destination(pr_number, issue_number, exact_head_sha)
    final_attempted, final_impossible = _connected_human_notice_evidence(
        reason, target_list
    )
    if final_attempted != connected_attempted or final_impossible != connected_impossible:
        raise RuntimeError("Connected human-only condition changed before publication")
    if _existing_internal_record(record_path) != record:
        raise RuntimeError("human-only audit record changed before publication")
    comment(pr_number, body)
'''
    new_notice = '''    final_attempted, final_impossible = _connected_human_notice_evidence(
        reason, target_list
    )
    if final_attempted != connected_attempted or final_impossible != connected_impossible:
        raise RuntimeError("Connected human-only condition changed before publication")
    if _existing_internal_record(record_path) != record:
        raise RuntimeError("human-only audit record changed before publication")
    _validated_notice_destination(pr_number, issue_number, exact_head_sha)
    comment(pr_number, body)
'''
    content = replace_once(content, old_notice, new_notice, "notice publication ordering")

    old_final = '''        if not exact_codex_clean(pr_number, sha):
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
    new_final = '''        if not exact_codex_clean(pr_number, sha):
            continue
        merge_candidate = _live_pr(pr_number, sha)
        final_issue_number, _, _, final_scope_error = source_and_scope(merge_candidate)
        if final_issue_number != issue_number or final_scope_error:
            continue
        if (
            merge_candidate.get("mergeable") is not True
            or not trusted_candidate(merge_candidate)
            or parse_issue_number(merge_candidate.get("body") or "") != issue_number
        ):
            continue
        gh(
'''
    return replace_once(content, old_final, new_final, "final source scope")


def repair_tests(content: str) -> str:
    content = replace_once(
        content,
        "import importlib\nimport json\n",
        "import importlib\nimport inspect\nimport json\n",
        "inspect import",
    )
    old_helper = '''    def _run_final_gate(
        self, snapshots, source_result, codex_clean_results=None
    ):
        codex_clean_results = codex_clean_results or [True, True, True]
'''
    new_helper = '''    def _run_final_gate(
        self,
        snapshots,
        source_result,
        codex_clean_results=None,
        late_source_result=None,
    ):
        codex_clean_results = codex_clean_results or [True, True, True]
        late_source_result = (
            source_result if late_source_result is None else late_source_result
        )
'''
    content = replace_once(content, old_helper, new_helper, "final gate helper")
    old_sources = '''                side_effect=[
                    (9, {"number": 9}, ["docs/probe.md"], None),
                    source_result,
                ],
'''
    new_sources = '''                side_effect=[
                    (9, {"number": 9}, ["docs/probe.md"], None),
                    source_result,
                    late_source_result,
                ],
'''
    content = replace_once(content, old_sources, new_sources, "late scope sequence")

    anchor = '''

if __name__ == "__main__":
    unittest.main()
'''
    regressions = '''

class FinalRaceOrderingRegressionTest(unittest.TestCase):
    def setUp(self):
        self.runtime = load_runtime()

    def test_cleared_blocker_and_retry_exhaustion_fail_closed(self):
        candidate = {"number": PR_NUMBER, "head": {"sha": SHA}, "mergeable": True}
        clean_scope = (9, {"number": 9}, ["docs/probe.md"], None)
        with (
            patch.object(self.runtime, "_live_pr", return_value=candidate),
            patch.object(self.runtime, "source_and_scope", return_value=clean_scope),
            patch.object(self.runtime, "exact_codex_state", return_value="clean"),
        ):
            with self.assertRaisesRegex(RuntimeError, "no longer supported"):
                self.runtime._revalidate_stop_reason(
                    PR_NUMBER, SHA, 9, "BLOCKING_CODEX_REVIEW"
                )
        with (
            patch.object(self.runtime, "_live_pr", return_value=candidate),
            patch.object(self.runtime, "source_and_scope", return_value=clean_scope),
            patch.object(
                self.runtime,
                "attestation_attempts",
                return_value=[{"success": True, "active": False, "run_id": 1}],
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "no longer supported"):
                self.runtime._revalidate_stop_reason(
                    PR_NUMBER, SHA, 9, "TRUSTED_ATTESTATION_RETRY_EXHAUSTED"
                )

    def test_unknown_stop_reason_fails_closed(self):
        candidate = {"number": PR_NUMBER, "head": {"sha": SHA}, "mergeable": True}
        with (
            patch.object(self.runtime, "_live_pr", return_value=candidate),
            patch.object(
                self.runtime,
                "source_and_scope",
                return_value=(9, {"number": 9}, ["docs/probe.md"], None),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "no independent live"):
                self.runtime._revalidate_stop_reason(
                    PR_NUMBER, SHA, 9, "AMBIGUOUS_TECHNICAL_STATE"
                )

    def test_final_network_and_merge_ordering(self):
        notice = inspect.getsource(self.runtime.human_only_notice)
        publication = notice.rsplit("if trusted_duplicate:", 1)[1]
        connected = publication.rfind("_connected_human_notice_evidence")
        record = publication.rfind("_existing_internal_record")
        destination = publication.rfind("_validated_notice_destination")
        publish = publication.rfind("comment(pr_number, body)")
        self.assertTrue(connected < record < destination < publish)

        supervise = inspect.getsource(self.runtime.supervise)
        last_codex = supervise.rfind("if not exact_codex_clean(pr_number, sha):")
        last_scope = supervise.rfind("source_and_scope(merge_candidate)")
        merge = supervise.rfind('f"repos/{REPO}/pulls/{pr_number}/merge"')
        self.assertTrue(last_codex < last_scope < merge)
'''
    return replace_once(content, anchor, regressions + anchor, "regression insertion")


def main() -> int:
    pull = api(f"repos/{REPOSITORY}/pulls/{PR_NUMBER}")
    require_target(pull)
    commit = api(f"repos/{REPOSITORY}/git/commits/{EXPECTED_HEAD}")
    tree_sha = (commit.get("tree") or {}).get("sha")
    if not tree_sha:
        raise RuntimeError("Expected commit has no tree")
    tree = api(f"repos/{REPOSITORY}/git/trees/{tree_sha}?recursive=1")
    targets = {RUNTIME_PATH: repair_runtime, TEST_PATH: repair_tests}
    entries = {
        item.get("path"): item
        for item in tree.get("tree") or []
        if item.get("path") in targets
    }
    if set(entries) != set(targets):
        raise RuntimeError("Expected tree lacks the exact two repair paths")

    updates = []
    for path, repair in targets.items():
        entry = entries[path]
        if entry.get("type") != "blob" or entry.get("mode") not in {"100644", "100755"}:
            raise RuntimeError(f"{path} is not a regular file")
        metadata = api(f"repos/{REPOSITORY}/contents/{path}?ref={EXPECTED_HEAD}")
        if metadata.get("sha") != entry.get("sha") or metadata.get("encoding") != "base64":
            raise RuntimeError(f"Immutable blob/tree binding failed for {path}")
        encoded = "".join((metadata.get("content") or "").split())
        content = base64.b64decode(encoded.encode("ascii"), validate=True).decode("utf-8")
        repaired = repair(content)
        compile(repaired, path, "exec")
        blob = api_json(
            "POST",
            f"repos/{REPOSITORY}/git/blobs",
            {"content": repaired, "encoding": "utf-8"},
        )
        updates.append(
            {"path": path, "mode": entry["mode"], "type": "blob", "sha": blob["sha"]}
        )

    new_tree = api_json(
        "POST",
        f"repos/{REPOSITORY}/git/trees",
        {"base_tree": tree_sha, "tree": updates},
    )
    new_commit = api_json(
        "POST",
        f"repos/{REPOSITORY}/git/commits",
        {
            "message": "Close final stop, scope, and notice races",
            "tree": new_tree["sha"],
            "parents": [EXPECTED_HEAD],
        },
    )
    require_target(api(f"repos/{REPOSITORY}/pulls/{PR_NUMBER}"))
    api_json(
        "PATCH",
        f"repos/{REPOSITORY}/git/refs/heads/{TARGET_BRANCH}",
        {"sha": new_commit["sha"], "force": False},
    )
    print(f"Repaired PR #{PR_NUMBER} to {new_commit['sha']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

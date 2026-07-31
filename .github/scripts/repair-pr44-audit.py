#!/usr/bin/env python3
"""Apply the fixed immutable audit repair to PR #44 without executing candidate code."""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess

REPOSITORY = os.environ["GITHUB_REPOSITORY"]
TARGET_PR = int(os.environ["TARGET_PR"])
TARGET_BRANCH = os.environ["TARGET_BRANCH"]
EXPECTED_HEAD = os.environ["EXPECTED_HEAD"]
TARGET_PATHS = (
    "scripts/supervisor_runtime.py",
    "tests/test_runtime_human_notice.py",
)

CONNECTED_OLD = '''def _connected_repository_creation_evidence(
    targets: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if len(targets) != 2 or any("/" not in target for target in targets):
        raise ValueError("repository-creation audit requires exactly two owner/name targets")
    attempted: list[str] = []
    impossible: list[str] = []
    for target in targets:
        path = f"repos/{target}"
'''

CONNECTED_NEW = '''def _canonical_repository_target(target: str) -> str:
    if target != target.strip():
        raise ValueError("repository target must not contain surrounding whitespace")
    parts = target.split("/")
    if len(parts) != 2 or any(not part for part in parts):
        raise ValueError("repository target must be exactly one owner/name pair")
    if any(part in {".", ".."} for part in parts):
        raise ValueError("repository target contains an unsafe path component")
    if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", part) for part in parts):
        raise ValueError("repository target contains unsupported characters")
    return f"{parts[0]}/{parts[1]}"


def _canonical_repository_targets(targets: tuple[str, ...]) -> tuple[str, str]:
    if len(targets) != 2:
        raise ValueError("repository-creation audit requires exactly two owner/name targets")
    canonical = tuple(_canonical_repository_target(target) for target in targets)
    if len(set(canonical)) != 2:
        raise ValueError("repository-creation audit requires two distinct repositories")
    return canonical


def _connected_repository_creation_evidence(
    targets: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    canonical_targets = _canonical_repository_targets(targets)
    attempted: list[str] = []
    impossible: list[str] = []
    for target in canonical_targets:
        path = f"repos/{target}"
'''

FORMAT_OLD = '''    if reason == "HUMAN_ONLY_ACCOUNT_LEVEL_REPOSITORY_CREATION_UI_UNAVAILABLE":
        if len(target_list) != 2 or any("/" not in item for item in target_list):
            raise ValueError("repository-creation notice requires exactly two owner/name targets")
'''
FORMAT_NEW = '''    if reason == "HUMAN_ONLY_ACCOUNT_LEVEL_REPOSITORY_CREATION_UI_UNAVAILABLE":
        canonical_targets = _canonical_repository_targets(target_list)
        if canonical_targets != target_list:
            raise ValueError("repository-creation notice targets must be canonical owner/name pairs")
'''

ISSUE_OLD = '''    issue_state = "not-applicable"
    authorization_state = "not-applicable"
    if issue_number:
        issue = api(f"repos/{REPO}/issues/{issue_number}")
        issue_body = issue.get("body") or ""
        issue_state = f"state={issue.get('state', 'unknown')},trusted_author={trusted_source_issue(issue)}"
        authorization_state = (
            "incomplete-path-evidence"
            if changed is None
            else (
                f"all_paths={scope_is_authorized(changed, issue_body)},"
                f"protected_paths={protected_scope_is_authorized(changed, issue_body)}"
            )
        )
'''
ISSUE_NEW = '''    issue_state = "not-applicable"
    authorization_state = "not-applicable"
    issue_trusted: bool | None = None
    all_paths_authorized: bool | None = None
    protected_paths_authorized: bool | None = None
    if issue_number:
        issue = api(f"repos/{REPO}/issues/{issue_number}")
        issue_body = issue.get("body") or ""
        issue_trusted = trusted_source_issue(issue)
        issue_state = f"state={issue.get('state', 'unknown')},trusted_author={issue_trusted}"
        if changed is None:
            authorization_state = "incomplete-path-evidence"
        else:
            all_paths_authorized = scope_is_authorized(changed, issue_body)
            protected_paths_authorized = protected_scope_is_authorized(
                changed, issue_body
            )
            authorization_state = (
                f"all_paths={all_paths_authorized},"
                f"protected_paths={protected_paths_authorized}"
            )
'''

FINAL_OLD = '''    final_pr = _live_pr(pr_number, sha)
    mergeable = final_pr.get("mergeable")
    mergeable_state = str(final_pr.get("mergeable_state") or "unknown")
    if reason == "MERGE_NOT_READY" and mergeable is not False:
        raise RuntimeError("MERGE_NOT_READY is no longer supported by live mergeability")

    return {
'''
FINAL_NEW = '''    final_pr = _live_pr(pr_number, sha)
    mergeable = final_pr.get("mergeable")
    mergeable_state = str(final_pr.get("mergeable_state") or "unknown")
    if reason == "MERGE_NOT_READY" and mergeable is not False:
        raise RuntimeError("MERGE_NOT_READY is no longer supported by live mergeability")
    if reason == "UNAUTHORIZED_CHANGED_PATH" and not (
        issue_trusted is True
        and changed is not None
        and all_paths_authorized is False
    ):
        raise RuntimeError(
            "UNAUTHORIZED_CHANGED_PATH is no longer supported by fresh Issue authorization"
        )
    if reason == "UNAUTHORIZED_PROTECTED_PATH" and not (
        issue_trusted is True
        and changed is not None
        and any(is_protected(path) for path in changed)
        and all_paths_authorized is True
        and protected_paths_authorized is False
    ):
        raise RuntimeError(
            "UNAUTHORIZED_PROTECTED_PATH is no longer supported by fresh Issue authorization"
        )

    return {
'''

TEST_INSERT = r'''

    def test_repository_targets_are_canonical_before_connected_query(self):
        invalid_targets = (
            ("owner/existing-repo/branches/missing", "other/repo"),
            ("/owner/repo", "other/repo"),
            ("owner/repo/", "other/repo"),
            ("owner//repo", "other/repo"),
            ("owner/re po", "other/repo"),
            ("owner/.", "other/repo"),
            ("owner/..", "other/repo"),
            (" owner/repo", "other/repo"),
            ("owner/repo", "owner/repo"),
        )
        for targets in invalid_targets:
            with (
                self.subTest(targets=targets),
                patch.object(self.runtime, "gh_result") as connected,
                self.assertRaises(ValueError),
            ):
                self.runtime._connected_repository_creation_evidence(targets)
            connected.assert_not_called()

    def test_cleared_ordinary_scope_stop_persists_nothing_and_does_not_close(self):
        patches = self.audit_dependencies()
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
            patch.object(self.runtime, "persist_internal_stop_record") as persist,
            patch.object(self.runtime, "gh") as gh,
        ):
            with self.assertRaisesRegex(RuntimeError, "no longer supported"):
                self.runtime.stop_report(
                    self.pr,
                    5,
                    "UNAUTHORIZED_CHANGED_PATH",
                    "stale ordinary denial",
                    close=True,
                )
        persist.assert_not_called()
        gh.assert_not_called()

    def test_cleared_protected_scope_stop_persists_nothing_and_does_not_close(self):
        protected_path = ".github/workflows/probe.yml"
        protected_issue = {
            "state": "open",
            "user": {"login": "owner"},
            "body": (
                "## Allowed paths\n"
                f"- `{protected_path}`\n\n"
                "<!-- foundation-protected-authorization\n"
                "paths:\n"
                f"- {protected_path}\n"
                "-->\n"
            ),
        }

        def protected_api(path):
            if path == "repos/example/foundation":
                return {"visibility": "public", "default_branch": "main"}
            if path == "repos/example/foundation/pulls/7":
                return self.pr
            if path == "repos/example/foundation/collaborators/owner/permission":
                return {"permission": "admin"}
            if path == "repos/example/foundation/issues/5":
                return protected_issue
            if "/actions/workflows/" in path:
                return {"id": 1, "state": "active"}
            raise AssertionError(path)

        with (
            patch.object(self.runtime, "api", side_effect=protected_api),
            patch.object(self.runtime, "changed_paths", return_value=[protected_path]),
            patch.object(self.runtime, "attestation_attempts", return_value=[]),
            patch.object(
                self.runtime, "native_workflow_evidence", return_value=(True, [])
            ),
            patch.object(self.runtime, "_sanitized_check_evidence", return_value="[]"),
            patch.object(
                self.runtime,
                "exact_codex_evidence",
                return_value={
                    "state": "pending",
                    "timestamp": None,
                    "request_timestamp": None,
                },
            ),
            patch.object(self.runtime, "unresolved_review_threads", return_value=False),
            patch.object(self.runtime, "persist_internal_stop_record") as persist,
            patch.object(self.runtime, "gh") as gh,
        ):
            with self.assertRaisesRegex(RuntimeError, "no longer supported"):
                self.runtime.stop_report(
                    self.pr,
                    5,
                    "UNAUTHORIZED_PROTECTED_PATH",
                    "stale protected denial",
                    close=True,
                )
        persist.assert_not_called()
        gh.assert_not_called()
'''

TEST_MARKER = '\n\nif __name__ == "__main__":\n    unittest.main()\n'


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


def main() -> None:
    pr = api(f"repos/{REPOSITORY}/pulls/{TARGET_PR}")
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    if pr.get("state") != "open":
        print("Target Pull Request is no longer open; nothing to repair.")
        return
    if (head.get("repo") or {}).get("full_name") != REPOSITORY:
        raise RuntimeError("Target Pull Request is not same-repository")
    if (base.get("repo") or {}).get("full_name") != REPOSITORY:
        raise RuntimeError("Target Pull Request base is not same-repository")
    if head.get("ref") != TARGET_BRANCH:
        raise RuntimeError("Target Pull Request branch changed")
    if head.get("sha") != EXPECTED_HEAD:
        print("Target head already moved; exact repair is no longer applicable.")
        return

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
        metadata = api(f"repos/{REPOSITORY}/contents/{path}?ref={EXPECTED_HEAD}")
        if metadata.get("sha") != entries[path].get("sha"):
            raise RuntimeError(f"Immutable blob/tree mismatch for {path}")
        if metadata.get("encoding") != "base64":
            raise RuntimeError(f"Unexpected content encoding for {path}")
        texts[path] = base64.b64decode(
            (metadata.get("content") or "").encode("ascii")
        ).decode("utf-8")

    runtime = texts["scripts/supervisor_runtime.py"]
    for old, new, label in (
        (CONNECTED_OLD, CONNECTED_NEW, "connected repository helper"),
        (FORMAT_OLD, FORMAT_NEW, "notice formatter"),
        (ISSUE_OLD, ISSUE_NEW, "fresh Issue authorization state"),
        (FINAL_OLD, FINAL_NEW, "fresh stop-reason support"),
    ):
        if runtime.count(old) != 1:
            raise RuntimeError(f"{label} anchor count is not exactly one")
        runtime = runtime.replace(old, new, 1)
    compile(runtime, "scripts/supervisor_runtime.py", "exec")

    tests = texts["tests/test_runtime_human_notice.py"]
    if "test_repository_targets_are_canonical_before_connected_query" in tests:
        print("Focused audit repair tests already exist; nothing to do.")
        return
    if tests.count(TEST_MARKER) != 1:
        raise RuntimeError("Test insertion marker count is not exactly one")
    tests = tests.replace(TEST_MARKER, TEST_INSERT + TEST_MARKER, 1)
    compile(tests, "tests/test_runtime_human_notice.py", "exec")

    replacements = {
        "scripts/supervisor_runtime.py": runtime,
        "tests/test_runtime_human_notice.py": tests,
    }
    tree_items = []
    for path in TARGET_PATHS:
        blob = api_json(
            "POST",
            f"repos/{REPOSITORY}/git/blobs",
            {"content": replacements[path], "encoding": "utf-8"},
        )
        mode = entries[path].get("mode")
        if mode not in {"100644", "100755"}:
            raise RuntimeError(f"Unexpected immutable mode for {path}: {mode}")
        tree_items.append(
            {"path": path, "mode": mode, "type": "blob", "sha": blob["sha"]}
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
            "message": "Validate canonical repository targets and fresh stop reasons",
            "tree": new_tree["sha"],
            "parents": [EXPECTED_HEAD],
        },
    )

    live = api(f"repos/{REPOSITORY}/pulls/{TARGET_PR}")
    live_head = live.get("head") or {}
    live_base = live.get("base") or {}
    if live.get("state") != "open":
        raise RuntimeError("Target Pull Request closed before exact ref update")
    if (live_head.get("repo") or {}).get("full_name") != REPOSITORY:
        raise RuntimeError("Target Pull Request lost same-repository head provenance")
    if (live_base.get("repo") or {}).get("full_name") != REPOSITORY:
        raise RuntimeError("Target Pull Request lost same-repository base provenance")
    if live_head.get("ref") != TARGET_BRANCH or live_head.get("sha") != EXPECTED_HEAD:
        raise RuntimeError("Target Pull Request moved before exact ref update")

    api_json(
        "PATCH",
        f"repos/{REPOSITORY}/git/refs/heads/{TARGET_BRANCH}",
        {"sha": new_commit["sha"], "force": False},
    )
    print(f"Repaired PR #{TARGET_PR} from {EXPECTED_HEAD} to {new_commit['sha']}.")


if __name__ == "__main__":
    main()

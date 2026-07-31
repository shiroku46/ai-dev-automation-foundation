#!/usr/bin/env python3
"""Apply the fixed immutable final race repair to PR #44."""
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
    "tests/test_runtime_scope_and_checks.py",
)

RUNTIME_OLD = '''        merge_candidate = _live_pr(pr_number, sha)
        if merge_candidate.get("mergeable") is not True or not trusted_candidate(
            merge_candidate
        ):
            continue
        gh(
'''

RUNTIME_NEW = '''        if not exact_codex_clean(pr_number, sha):
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

TEST_SIGNATURE_OLD = '''    def _run_final_gate(self, snapshots, source_result):
'''
TEST_SIGNATURE_NEW = '''    def _run_final_gate(self, snapshots, source_result, codex_clean=True):
'''

TEST_CODEX_PATCH_OLD = '''            patch.object(self.runtime, "exact_codex_clean", return_value=True),
'''
TEST_CODEX_PATCH_NEW = '''            patch.object(
                self.runtime,
                "exact_codex_clean",
                side_effect=codex_clean if isinstance(codex_clean, list) else None,
                return_value=codex_clean if not isinstance(codex_clean, list) else None,
            ),
'''

TEST_INSERT_ANCHOR = '''    def test_clean_revalidated_scope_reaches_expected_sha_merge(self):
'''
TEST_INSERT = '''    def test_late_source_link_edit_blocks_final_merge(self):
        clean = self._candidate()
        relinked = self._candidate()
        relinked["body"] = "Closes #10"
        gh = self._run_final_gate(
            [clean, clean, clean, clean, relinked],
            (9, {"number": 9}, ["docs/probe.md"], None),
        )
        self.assertFalse(
            any(
                len(call.args) >= 4
                and call.args[0:3] == ("api", "--method", "PUT")
                and call.args[3].endswith("/merge")
                for call in gh.call_args_list
            )
        )

    def test_late_codex_blocker_after_scope_audit_blocks_merge(self):
        clean = self._candidate()
        gh = self._run_final_gate(
            [clean, clean, clean],
            (9, {"number": 9}, ["docs/probe.md"], None),
            codex_clean=[True, False],
        )
        self.assertFalse(
            any(
                len(call.args) >= 4
                and call.args[0:3] == ("api", "--method", "PUT")
                and call.args[3].endswith("/merge")
                for call in gh.call_args_list
            )
        )

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
        print("Target head already moved; exact repair is no longer applicable.")
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
        metadata = api(f"repos/{REPOSITORY}/contents/{path}?ref={EXPECTED_HEAD}")
        if metadata.get("sha") != entries[path].get("sha"):
            raise RuntimeError(f"Immutable blob/tree mismatch for {path}")
        if metadata.get("encoding") != "base64":
            raise RuntimeError(f"Unexpected content encoding for {path}")
        texts[path] = base64.b64decode(
            (metadata.get("content") or "").encode("ascii")
        ).decode("utf-8")

    runtime = texts["scripts/supervisor_runtime.py"]
    if runtime.count(RUNTIME_OLD) != 1:
        raise RuntimeError("Final merge-gate replacement anchor count is not exactly one")
    runtime = runtime.replace(RUNTIME_OLD, RUNTIME_NEW, 1)
    compile(runtime, "scripts/supervisor_runtime.py", "exec")

    tests = texts["tests/test_runtime_scope_and_checks.py"]
    for old, new, label in (
        (TEST_SIGNATURE_OLD, TEST_SIGNATURE_NEW, "test helper signature"),
        (TEST_CODEX_PATCH_OLD, TEST_CODEX_PATCH_NEW, "test Codex patch"),
    ):
        if tests.count(old) != 1:
            raise RuntimeError(f"{label} anchor count is not exactly one")
        tests = tests.replace(old, new, 1)
    if "test_late_source_link_edit_blocks_final_merge" in tests:
        print("Focused final-race tests already exist; nothing to do.")
        return
    if tests.count(TEST_INSERT_ANCHOR) != 1:
        raise RuntimeError("Focused test insertion anchor count is not exactly one")
    tests = tests.replace(TEST_INSERT_ANCHOR, TEST_INSERT + TEST_INSERT_ANCHOR, 1)
    compile(tests, "tests/test_runtime_scope_and_checks.py", "exec")

    replacements = {
        "scripts/supervisor_runtime.py": runtime,
        "tests/test_runtime_scope_and_checks.py": tests,
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
            "message": "Revalidate final source link and Codex evidence",
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

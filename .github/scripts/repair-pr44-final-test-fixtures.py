#!/usr/bin/env python3
"""Apply the fixed immutable final-gate test fixture repair to PR #44."""
from __future__ import annotations

import base64
import json
import os
import subprocess

REPOSITORY = os.environ["GITHUB_REPOSITORY"]
TARGET_PR = 44
TARGET_BRANCH = "fix/issue-36-human-notice-contract"
EXPECTED_HEAD = "d8e588e0ffa52a524918ead4aed3b0de4753c22d"
TARGET_PATH = "tests/test_runtime_scope_and_checks.py"
SOURCE_ISSUE = 36
OWNER = "shiroku46"


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
    if base.get("ref") != "main":
        raise RuntimeError("Target Pull Request base branch changed")
    if head.get("ref") != TARGET_BRANCH:
        raise RuntimeError("Target Pull Request branch changed")
    if head.get("sha") != EXPECTED_HEAD:
        print("Target head moved; exact fixture repair is no longer applicable.")
        raise SystemExit(0)
    if f"Closes #{SOURCE_ISSUE}" not in (snapshot.get("body") or ""):
        raise RuntimeError("Target Pull Request source Issue binding changed")

    issue = api(f"repos/{REPOSITORY}/issues/{SOURCE_ISSUE}")
    if issue.get("pull_request"):
        raise RuntimeError("Trusted source is not an Issue")
    if (issue.get("user") or {}).get("login") != OWNER:
        raise RuntimeError("Trusted source Issue is not owner-authored")
    exact_line = f"- `{TARGET_PATH}`"
    if exact_line not in (issue.get("body") or "").splitlines():
        raise RuntimeError("Target path is no longer authorized by the source Issue")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one exact anchor, found {count}")
    return text.replace(old, new, 1)


def transform(source: str) -> str:
    source = replace_once(
        source,
        "import os\nimport subprocess\n",
        "import os\nfrom pathlib import Path\nimport subprocess\n",
        "Path import",
    )
    source = replace_once(
        source,
        "codex_clean_results = codex_clean_results or [True, True, True]",
        "codex_clean_results = codex_clean_results or [True]",
        "default exact Codex result sequence",
    )
    source = replace_once(
        source,
        """        gh = self._run_final_gate(\n            [clean, clean, clean, held],\n            (9, {\"number\": 9}, [\"docs/probe.md\"], None),\n        )\n""",
        """        gh = self._run_final_gate(\n            [clean, clean, clean, clean, held],\n            (9, {\"number\": 9}, [\"docs/probe.md\"], None),\n        )\n""",
        "final hold-state snapshots",
    )
    source = replace_once(
        source,
        """        gh = self._run_final_gate(\n            [clean, clean, clean, clean, clean, clean],\n            (9, {\"number\": 9}, [\"docs/probe.md\"], None),\n        )\n""",
        """        gh = self._run_final_gate(\n            [clean, clean, clean, clean, clean],\n            (9, {\"number\": 9}, [\"docs/probe.md\"], None),\n        )\n""",
        "clean final-gate snapshots",
    )
    source = replace_once(
        source,
        """        gh = self._run_final_gate(\n            [clean, clean, clean, clean],\n            (9, {\"number\": 9}, [\"docs/probe.md\"], None),\n            codex_clean_results=[True, False],\n        )\n""",
        """        gh = self._run_final_gate(\n            [clean, clean, clean],\n            (9, {\"number\": 9}, [\"docs/probe.md\"], None),\n            codex_clean_results=[False],\n        )\n""",
        "late exact-SHA Codex blocker fixture",
    )
    source = replace_once(
        source,
        'runtime = open(load_runtime().__file__, encoding="utf-8").read()',
        'runtime = Path(load_runtime().__file__).read_text(encoding="utf-8")',
        "context-managed source read",
    )
    compile(source, TARGET_PATH, "exec")
    return source


def main() -> None:
    require_target(api(f"repos/{REPOSITORY}/pulls/{TARGET_PR}"))

    commit = api(f"repos/{REPOSITORY}/git/commits/{EXPECTED_HEAD}")
    tree_sha = (commit.get("tree") or {}).get("sha")
    if not tree_sha:
        raise RuntimeError("Expected commit has no tree")
    tree = api(f"repos/{REPOSITORY}/git/trees/{tree_sha}?recursive=1")
    matches = [
        item for item in tree.get("tree") or [] if item.get("path") == TARGET_PATH
    ]
    if len(matches) != 1:
        raise RuntimeError("Expected tree does not contain exactly one target path")
    entry = matches[0]
    if entry.get("type") != "blob" or entry.get("mode") not in {"100644", "100755"}:
        raise RuntimeError("Target is not an expected regular text file")

    metadata = api(f"repos/{REPOSITORY}/contents/{TARGET_PATH}?ref={EXPECTED_HEAD}")
    if metadata.get("sha") != entry.get("sha") or metadata.get("encoding") != "base64":
        raise RuntimeError("Immutable target blob/tree binding failed")
    encoded = "".join((metadata.get("content") or "").split())
    source = base64.b64decode(encoded.encode("ascii"), validate=True).decode("utf-8")
    repaired = transform(source)
    if repaired == source:
        raise RuntimeError("Fixture repair produced no change")

    require_target(api(f"repos/{REPOSITORY}/pulls/{TARGET_PR}"))
    blob = api_json(
        "POST",
        f"repos/{REPOSITORY}/git/blobs",
        {"content": repaired, "encoding": "utf-8"},
    )
    new_tree = api_json(
        "POST",
        f"repos/{REPOSITORY}/git/trees",
        {
            "base_tree": tree_sha,
            "tree": [
                {
                    "path": TARGET_PATH,
                    "mode": entry["mode"],
                    "type": "blob",
                    "sha": blob["sha"],
                }
            ],
        },
    )
    new_commit = api_json(
        "POST",
        f"repos/{REPOSITORY}/git/commits",
        {
            "message": "Align final merge-gate test fixtures with runtime order",
            "tree": new_tree["sha"],
            "parents": [EXPECTED_HEAD],
        },
    )

    require_target(api(f"repos/{REPOSITORY}/pulls/{TARGET_PR}"))
    api_json(
        "PATCH",
        f"repos/{REPOSITORY}/git/refs/heads/{TARGET_BRANCH}",
        {"sha": new_commit["sha"], "force": False},
    )
    print(f"Repaired PR #{TARGET_PR} to {new_commit['sha']}.")


if __name__ == "__main__":
    main()

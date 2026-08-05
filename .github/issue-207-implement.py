#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8", newline="\n")


def replace_once(content: str, old: str, new: str, label: str) -> str:
    if content.count(old) != 1:
        raise SystemExit(f"{label}: expected one match, found {content.count(old)}")
    return content.replace(old, new, 1)


# Bootstrap: install target-owned config once, preserve it thereafter, and exclude it from lock drift.
path = "bootstrap/generator.py"
content = read(path)
content = replace_once(
    content,
    'PRESERVE_IF_PRESENT = frozenset(\n    {"README.md", "LICENSE", "AGENTS.md", "CLAUDE.md", "SECURITY.md"}\n)\nMANAGED_FILES = (',
    'PRESERVE_IF_PRESENT = frozenset(\n    {"README.md", "LICENSE", "AGENTS.md", "CLAUDE.md", "SECURITY.md"}\n)\nTARGET_OWNED_FILES = frozenset({".github/foundation-product-checks.json"})\nMANAGED_FILES = (',
    "bootstrap target-owned constant",
)
content = replace_once(
    content,
    '    "scripts/queue_retry_identity.py", "scripts/queue_event_guard.py",\n    "scripts/github_api_governor.py", "scripts/github_coordinator_supervisor.py",',
    '    "scripts/queue_retry_identity.py", "scripts/queue_event_guard.py",\n    "scripts/foundation_product_checks.py",\n    "scripts/github_api_governor.py", "scripts/github_coordinator_supervisor.py",',
    "bootstrap parser path",
)
content = replace_once(
    content,
    '    "scripts/supervisor_policy.py", "scripts/foundation_drift.py",\n    ".github/workflows/ci.yml", ".github/workflows/unit-tests.yml",',
    '    "scripts/supervisor_policy.py", "scripts/foundation_drift.py",\n    ".github/foundation-product-checks.json",\n    ".github/workflows/ci.yml", ".github/workflows/unit-tests.yml",',
    "bootstrap config path",
)
old_loop = '''        if destination.exists():
            if not destination.is_file():
                collisions.append(relative)
                entries.append(PlanEntry(relative, "collision", source_digest, None))
                continue
            target_digest = _sha256_file(destination)
            if target_digest == source_digest:
                action = "unchanged"
                managed.append(relative)
            elif relative in authorized:
                action = "overwrite-authorized"
                writes.append(relative)
                managed.append(relative)
            elif locked.get(relative) == target_digest:
                action = "upgrade"
                writes.append(relative)
                managed.append(relative)
            elif mode == "existing-product" and relative in PRESERVE_IF_PRESENT:
                action = "preserved"
                preserved.append(relative)
            else:
                action = "collision"
                collisions.append(relative)
        else:
            action = "add"
            writes.append(relative)
            managed.append(relative)
'''
new_loop = '''        target_owned = relative in TARGET_OWNED_FILES
        if destination.exists():
            if not destination.is_file():
                collisions.append(relative)
                entries.append(PlanEntry(relative, "collision", source_digest, None))
                continue
            target_digest = _sha256_file(destination)
            if target_owned:
                action = "target-owned-unchanged" if target_digest == source_digest else "target-owned-preserved"
                preserved.append(relative)
            elif target_digest == source_digest:
                action = "unchanged"
                managed.append(relative)
            elif relative in authorized:
                action = "overwrite-authorized"
                writes.append(relative)
                managed.append(relative)
            elif locked.get(relative) == target_digest:
                action = "upgrade"
                writes.append(relative)
                managed.append(relative)
            elif mode == "existing-product" and relative in PRESERVE_IF_PRESENT:
                action = "preserved"
                preserved.append(relative)
            else:
                action = "collision"
                collisions.append(relative)
        else:
            action = "add-target-owned" if target_owned else "add"
            writes.append(relative)
            if target_owned:
                preserved.append(relative)
            else:
                managed.append(relative)
'''
content = replace_once(content, old_loop, new_loop, "bootstrap plan loop")
content = replace_once(
    content,
    '        if entry.action == "add":\n',
    '        if entry.action in {"add", "add-target-owned"}:\n',
    "bootstrap plan recheck",
)
content = replace_once(
    content,
    '- [ ] Keep target-owned files outside the managed lock.\n',
    '- [ ] Keep target-owned files outside the managed lock.\n- [ ] Configure required product workflows in `.github/foundation-product-checks.json`; the previous default-branch config judges each configuration-changing PR.\n',
    "bootstrap checklist product config",
)
write(path, content)

# Public export: allow product vocabulary only for authenticated generated targets.
path = "scripts/public_export_guard.py"
content = read(path)
content = replace_once(
    content,
    'SKIP_PARTS = {".git", "__pycache__", ".pytest_cache"}\nPATTERNS = {',
    'SKIP_PARTS = {".git", "__pycache__", ".pytest_cache"}\nGENERATED_TARGET_MARKER = "<!-- ai-dev-automation-foundation:generated-target -->"\nPATTERNS = {',
    "export marker",
)
content = replace_once(
    content,
    'def scan(root: Path) -> list[str]:\n    findings: list[str] = []\n',
    'def is_generated_target(root: Path) -> bool:\n    checklist = root / "INSTALL_CHECKLIST.md"\n    return checklist.is_file() and GENERATED_TARGET_MARKER in checklist.read_text(encoding="utf-8")\n\ndef scan(root: Path) -> list[str]:\n    findings: list[str] = []\n    generated_target = is_generated_target(root)\n',
    "export mode",
)
content = replace_once(
    content,
    '        for name, pattern in PATTERNS.items():\n            for match in pattern.finditer(cleaned):',
    '        for name, pattern in PATTERNS.items():\n            if generated_target and name == "product-specific":\n                continue\n            for match in pattern.finditer(cleaned):',
    "export source-only product pattern",
)
write(path, content)

# Validator: require/parse config and exclude target-owned config from lock hashes.
path = "scripts/validate_repository.py"
content = read(path)
content = replace_once(
    content,
    'from pathlib import Path\n\nROOT =',
    'from pathlib import Path\n\nfrom scripts.foundation_product_checks import (\n    CONFIG_PATH as PRODUCT_CHECKS_PATH,\n    ProductCheckConfigError,\n    parse_product_checks,\n)\n\nROOT =',
    "validator import",
)
content = replace_once(
    content,
    '    "scripts/queue_retry_identity.py", "scripts/queue_event_guard.py",\n    "scripts/github_api_governor.py", "scripts/github_coordinator_supervisor.py",',
    '    "scripts/queue_retry_identity.py", "scripts/queue_event_guard.py",\n    "scripts/foundation_product_checks.py",\n    "scripts/github_api_governor.py", "scripts/github_coordinator_supervisor.py",',
    "validator parser required",
)
content = replace_once(
    content,
    '    ".github/workflows/ci.yml", ".github/workflows/unit-tests.yml",',
    '    PRODUCT_CHECKS_PATH,\n    ".github/workflows/ci.yml", ".github/workflows/unit-tests.yml",',
    "validator config required",
)
content = replace_once(
    content,
    '    if missing:\n        raise ValidationError("missing files: " + ", ".join(missing))\n\n    if generated_target:',
    '    if missing:\n        raise ValidationError("missing files: " + ", ".join(missing))\n    try:\n        parse_product_checks((ROOT / PRODUCT_CHECKS_PATH).read_bytes())\n    except ProductCheckConfigError as exc:\n        raise ValidationError(f"product check configuration is invalid: {exc}") from exc\n\n    if generated_target:',
    "validator parse config",
)
content = replace_once(
    content,
    '        lock_required = (REQUIRED - preserved) | {"INSTALL_CHECKLIST.md"}\n',
    '        target_owned = {PRODUCT_CHECKS_PATH}\n        lock_required = (REQUIRED - preserved - target_owned) | {"INSTALL_CHECKLIST.md"}\n',
    "validator target-owned lock exclusion",
)
content = replace_once(
    content,
    '            "scripts/queue_event_guard.py", "scripts/github_api_governor.py",\n',
    '            "scripts/queue_event_guard.py", "scripts/foundation_product_checks.py",\n            "scripts/github_api_governor.py",\n',
    "validator generator markers",
)
write(path, content)

# Coordinator: use captured default config and exact-head product workflow runs.
path = "scripts/github_coordinator_supervisor.py"
content = read(path)
content = replace_once(content, "import argparse\n", "import argparse\nimport base64\nimport binascii\n", "coordinator imports")
content = replace_once(
    content,
    'from typing import Any, Iterable, Mapping, Protocol, Sequence\n\nSHA_RE =',
    'from typing import Any, Iterable, Mapping, Protocol, Sequence\n\nfrom scripts.foundation_product_checks import (\n    CONFIG_PATH as PRODUCT_CHECKS_PATH,\n    ProductCheckConfigError,\n    parse_product_checks,\n)\n\nSHA_RE =',
    "coordinator product imports",
)
content = replace_once(
    content,
    'PROTECTED_EXACT = {\n    "SECURITY.md",',
    'PROTECTED_EXACT = {\n    PRODUCT_CHECKS_PATH,\n    "SECURITY.md",',
    "coordinator protected config",
)
content = replace_once(
    content,
    '    def file_blob(self, path: str, ref: str) -> str: ...\n',
    '    def file_content(self, path: str, ref: str) -> bytes: ...\n    def file_blob(self, path: str, ref: str) -> str: ...\n',
    "coordinator protocol content",
)
start = content.index("def _check_snapshot(")
end = content.index("\n\ndef _authorization_snapshot", start)
new_check = '''def _check_snapshot(
    runs: Sequence[Mapping[str, Any]],
    head: str,
    repo: str,
    number: int,
    required_checks: Sequence[str],
) -> tuple[tuple[str, str, int], ...]:
    required = tuple(required_checks)
    if not required or len(required) != len(set(required)):
        raise SupervisorError("required check identity is invalid")
    required_set = set(required)
    latest: dict[str, tuple[str, int, str]] = {}
    for run in runs:
        if run.get("event") != "pull_request" or run.get("head_sha") != head:
            continue
        if str(((run.get("repository") or {}).get("full_name")) or "").casefold() != repo.casefold():
            continue
        prs = run.get("pull_requests")
        if not isinstance(prs, list) or not any(
            isinstance(item, dict) and item.get("number") == number for item in prs
        ):
            continue
        name = str(run.get("name") or "")
        if name not in required_set:
            continue
        run_id = run.get("id")
        updated = str(run.get("updated_at") or run.get("created_at") or "")
        if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0 or not updated:
            raise SupervisorError("workflow evidence is malformed")
        state = str(run.get("conclusion") or "pending") if run.get("status") == "completed" else "pending"
        candidate = (updated, run_id, state)
        if name not in latest or candidate[:2] > latest[name][:2]:
            latest[name] = candidate
    return tuple(
        (name, latest.get(name, ("", 0, "missing"))[2], latest.get(name, ("", 0, "missing"))[1])
        for name in required
    )
'''
content = content[:start] + new_check + content[end:]
insert_at = content.index("\ndef evaluate(")
helper = '''

def _product_checks(client: Client, ref: str, where: str):
    raw = client.file_content(PRODUCT_CHECKS_PATH, ref)
    try:
        return raw, parse_product_checks(raw)
    except ProductCheckConfigError as exc:
        raise SupervisorError(f"{where} product check configuration is invalid") from exc
'''
content = content[:insert_at] + helper + content[insert_at:]
content = replace_once(
    content,
    '    number, head = _pr_identity(pr, repo, default_branch)\n    pr_body = _text(pr.get("body"), "PR body")\n',
    '    number, head = _pr_identity(pr, repo, default_branch)\n    default_product_raw, default_product_checks = _product_checks(client, default_sha, "default")\n    candidate_product_raw, _candidate_product_checks = _product_checks(client, head, "candidate")\n    configured_workflows = {**CHECK_WORKFLOWS, **{item.name: item.workflow for item in default_product_checks}}\n    required_checks = tuple(configured_workflows)\n    product_check_names = {item.name for item in default_product_checks}\n    pr_body = _text(pr.get("body"), "PR body")\n',
    "coordinator load configs",
)
old_workflows = '''    for name, workflow_path in CHECK_WORKFLOWS.items():
        if client.file_blob(workflow_path, head) != client.file_blob(workflow_path, default_sha):
            raise SupervisorError(f"{name} workflow differs from the default-branch definition")
    checks = _check_snapshot(client.workflow_runs(head), head, repo, number)
    if any(state not in PASSING for _, state, _ in checks):
        raise SupervisorError("required exact-head checks are not all successful")
'''
new_workflows = '''    workflow_blobs = tuple(
        (name, workflow_path, client.file_blob(workflow_path, head), client.file_blob(workflow_path, default_sha))
        for name, workflow_path in configured_workflows.items()
    )
    for name, _workflow_path, candidate_blob, default_blob in workflow_blobs:
        if candidate_blob != default_blob:
            raise SupervisorError(f"{name} workflow differs from the default-branch definition")
    checks = _check_snapshot(client.workflow_runs(head), head, repo, number, required_checks)
    if any(
        state != "success" if name in product_check_names else state not in PASSING
        for name, state, _ in checks
    ):
        raise SupervisorError("required exact-head checks are not all successful")
'''
content = replace_once(content, old_workflows, new_workflows, "coordinator product workflows")
content = replace_once(
    content,
    '    if client.default_branch_sha(default_branch) != default_sha:\n        raise SupervisorError("default branch changed during evaluation")\n',
    '    if client.default_branch_sha(default_branch) != default_sha:\n        raise SupervisorError("default branch changed during evaluation")\n    if client.file_content(PRODUCT_CHECKS_PATH, default_sha) != default_product_raw:\n        raise SupervisorError("default product check configuration changed during evaluation")\n    if client.file_content(PRODUCT_CHECKS_PATH, head) != candidate_product_raw:\n        raise SupervisorError("candidate product check configuration changed during evaluation")\n    if tuple(\n        (name, workflow_path, client.file_blob(workflow_path, head), client.file_blob(workflow_path, default_sha))\n        for name, workflow_path in configured_workflows.items()\n    ) != workflow_blobs:\n        raise SupervisorError("product workflow definitions changed during evaluation")\n',
    "coordinator config race",
)
content = replace_once(
    content,
    '    if _check_snapshot(client.workflow_runs(head), head, repo, number) != checks:\n',
    '    if _check_snapshot(client.workflow_runs(head), head, repo, number, required_checks) != checks:\n',
    "coordinator check race",
)
content = replace_once(
    content,
    '    def file_blob(self, path, ref):\n        value = self._api(f"repos/{self.repo}/contents/{urllib.parse.quote(path, safe=\'/\')}?ref={ref}")\n        return _sha(value.get("sha"), "workflow blob")\n',
    '    def file_content(self, path, ref):\n        value = self._api(f"repos/{self.repo}/contents/{urllib.parse.quote(path, safe=\'/\')}?ref={ref}")\n        if value.get("type") != "file" or value.get("encoding") != "base64":\n            raise SupervisorError("configuration content response is invalid")\n        encoded = str(value.get("content") or "").replace("\\n", "")\n        try:\n            return base64.b64decode(encoded, validate=True)\n        except (ValueError, binascii.Error) as exc:\n            raise SupervisorError("configuration content encoding is invalid") from exc\n    def file_blob(self, path, ref):\n        value = self._api(f"repos/{self.repo}/contents/{urllib.parse.quote(path, safe=\'/\')}?ref={ref}")\n        return _sha(value.get("sha"), "file blob")\n',
    "coordinator GhClient content",
)
write(path, content)

# Focused coordinator fixtures.
path = "tests/test_github_coordinator_supervisor.py"
content = read(path)
content = replace_once(
    content,
    'import copy\nimport unittest\n',
    'import copy\nimport json\nimport unittest\n',
    "coordinator test json",
)
content = replace_once(
    content,
    'from scripts.github_coordinator_supervisor import (\n',
    'from scripts.foundation_product_checks import CONFIG_PATH as PRODUCT_CHECKS_PATH\nfrom scripts.github_coordinator_supervisor import (\n',
    "coordinator test config import",
)
content = replace_once(
    content,
    '        self.blobs = {\n',
    '        empty_config = json.dumps({"schema_version": 1, "checks": []}).encode()\n        self.contents = {\n            (PRODUCT_CHECKS_PATH, DEFAULT_SHA): empty_config,\n            (PRODUCT_CHECKS_PATH, HEAD_SHA): empty_config,\n        }\n        self.blobs = {\n',
    "coordinator fake contents",
)
content = replace_once(
    content,
    '        self.issue_reads = self.pr_reads = self.run_reads = self.thread_reads = 0\n        self.issue_race = self.pr_race = self.run_race = self.thread_race = None\n',
    '        self.issue_reads = self.pr_reads = self.run_reads = self.thread_reads = self.content_reads = 0\n        self.issue_race = self.pr_race = self.run_race = self.thread_race = self.content_race = None\n',
    "coordinator fake counters",
)
content = replace_once(
    content,
    '    def file_blob(self, path, ref): return self.blobs[(path, ref)]\n',
    '    def file_content(self, path, ref):\n        self.content_reads += 1\n        if self.content_reads == 3 and self.content_race: self.content_race(self)\n        return self.contents[(path, ref)]\n    def file_blob(self, path, ref): return self.blobs[(path, ref)]\n',
    "coordinator fake content method",
)
content = replace_once(
    content,
    '        self.issue_reads = self.pr_reads = self.run_reads = self.thread_reads = 0\n',
    '        self.issue_reads = self.pr_reads = self.run_reads = self.thread_reads = self.content_reads = 0\n',
    "coordinator ready reset",
)
extra_tests = '''
    def configure_product_check(self, client, *, candidate_config=None):
        config = json.dumps({
            "schema_version": 1,
            "checks": [{"name": "Product CI", "workflow": ".github/workflows/product-ci.yml"}],
        }).encode()
        client.contents[(PRODUCT_CHECKS_PATH, DEFAULT_SHA)] = config
        client.contents[(PRODUCT_CHECKS_PATH, HEAD_SHA)] = candidate_config or config
        client.blobs[(".github/workflows/product-ci.yml", DEFAULT_SHA)] = "e" * 40
        client.blobs[(".github/workflows/product-ci.yml", HEAD_SHA)] = "e" * 40
        client.runs.append({
            "id": 103, "name": "Product CI", "event": "pull_request", "status": "completed",
            "conclusion": "success", "head_sha": HEAD_SHA, "updated_at": "2026-08-05T00:12:00Z",
            "repository": {"full_name": REPO}, "pull_requests": [{"number": 5}],
        })

    def test_default_configured_product_check_is_required(self):
        client = FakeClient(); self.configure_product_check(client)
        self.assertEqual(evaluate(client, REPO, 5).action, "merge")
        for state in ("failure", "pending"):
            broken = FakeClient(); self.configure_product_check(broken)
            run = next(item for item in broken.runs if item["name"] == "Product CI")
            if state == "pending":
                run.update(status="in_progress", conclusion=None)
            else:
                run["conclusion"] = state
            with self.subTest(state=state):
                with self.assertRaisesRegex(SupervisorError, "checks are not all successful"):
                    evaluate(broken, REPO, 5)
        missing = FakeClient(); self.configure_product_check(missing)
        missing.runs = [item for item in missing.runs if item["name"] != "Product CI"]
        with self.assertRaisesRegex(SupervisorError, "checks are not all successful"):
            evaluate(missing, REPO, 5)

    def test_product_run_must_be_explicitly_associated_and_workflow_immutable(self):
        client = FakeClient(); self.configure_product_check(client)
        next(item for item in client.runs if item["name"] == "Product CI")["pull_requests"] = []
        with self.assertRaisesRegex(SupervisorError, "checks are not all successful"):
            evaluate(client, REPO, 5)
        client = FakeClient(); self.configure_product_check(client)
        client.blobs[(".github/workflows/product-ci.yml", HEAD_SHA)] = "f" * 40
        with self.assertRaisesRegex(SupervisorError, "workflow differs"):
            evaluate(client, REPO, 5)

    def test_candidate_config_is_validated_but_does_not_judge_itself(self):
        client = FakeClient()
        future = json.dumps({
            "schema_version": 1,
            "checks": [{"name": "Future Check", "workflow": ".github/workflows/future.yml"}],
        }).encode()
        client.contents[(PRODUCT_CHECKS_PATH, HEAD_SHA)] = future
        self.assertEqual(evaluate(client, REPO, 5).action, "merge")
        client.contents[(PRODUCT_CHECKS_PATH, HEAD_SHA)] = b"not-json"
        with self.assertRaisesRegex(SupervisorError, "candidate product check configuration"):
            evaluate(client, REPO, 5)

    def test_product_config_and_definition_races_fail_closed(self):
        client = FakeClient(); self.configure_product_check(client)
        client.content_race = lambda value: value.contents.__setitem__(
            (PRODUCT_CHECKS_PATH, DEFAULT_SHA),
            json.dumps({"schema_version": 1, "checks": []}).encode(),
        )
        with self.assertRaisesRegex(SupervisorError, "configuration changed"):
            evaluate(client, REPO, 5)
'''
content = replace_once(
    content,
    '\n\nif __name__ == "__main__": unittest.main()\n',
    extra_tests + '\n\nif __name__ == "__main__": unittest.main()\n',
    "coordinator product tests",
)
write(path, content)

# Bootstrap tests.
path = "tests/test_bootstrap.py"
content = read(path)
content = replace_once(
    content,
    '    PRESERVE_IF_PRESENT,\n',
    '    PRESERVE_IF_PRESENT,\n    TARGET_OWNED_FILES,\n',
    "bootstrap test target owned import",
)
content = replace_once(
    content,
    '            "scripts/queue_event_guard.py",\n',
    '            "scripts/queue_event_guard.py",\n            "scripts/foundation_product_checks.py",\n            ".github/foundation-product-checks.json",\n',
    "bootstrap test required product config",
)
content = replace_once(
    content,
    '            self.assertEqual(set(paths), set(MANAGED_FILES) | {"INSTALL_CHECKLIST.md"})\n',
    '            self.assertEqual(set(paths), (set(MANAGED_FILES) - set(TARGET_OWNED_FILES)) | {"INSTALL_CHECKLIST.md"})\n            self.assertTrue(set(TARGET_OWNED_FILES).isdisjoint(paths))\n',
    "bootstrap test lock exclusion",
)
content = replace_once(
    content,
    '            self.assertEqual(set(plan.preserved), set(PRESERVE_IF_PRESENT))\n',
    '            self.assertEqual(set(plan.preserved), set(PRESERVE_IF_PRESENT) | set(TARGET_OWNED_FILES))\n',
    "bootstrap test preserved set",
)
product_test = '''
    def test_product_check_config_is_target_owned_and_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            render_new(target)
            config = target / ".github/foundation-product-checks.json"
            customized = '{"schema_version":1,"checks":[{"name":"Product CI","workflow":".github/workflows/product-ci.yml"}]}\n'
            config.write_text(customized, encoding="utf-8")
            plan = render(
                target,
                "owner",
                mode="existing-product",
                source_sha="b" * 40,
                installed_at="2026-08-06T00:00:00Z",
            )
            self.assertIn(".github/foundation-product-checks.json", plan.preserved)
            self.assertEqual(config.read_text(encoding="utf-8"), customized)
            lock = json.loads((target / LOCK_FILE).read_text(encoding="utf-8"))
            self.assertNotIn(
                ".github/foundation-product-checks.json",
                {item["path"] for item in lock["managed_files"]},
            )
            self.assertEqual(validate(target).returncode, 0)
'''
content = replace_once(
    content,
    '    def test_install_checklist_describes_github_only_non_destructive_path(self):\n',
    product_test + '\n    def test_install_checklist_describes_github_only_non_destructive_path(self):\n',
    "bootstrap target-owned test",
)
content = replace_once(
    content,
    '                "human_action_required: false",\n',
    '                "human_action_required: false",\n                ".github/foundation-product-checks.json",\n',
    "bootstrap checklist test",
)
write(path, content)

# Export tests.
path = "tests/test_export_guard.py"
content = '''import tempfile
import unittest
from pathlib import Path
from scripts.public_export_guard import GENERATED_TARGET_MARKER, scan


class ExportGuardTest(unittest.TestCase):
    def test_clean_and_secret_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("public content")
            self.assertEqual(scan(root), [])
            (root / "bad.txt").write_text("api_key = abcdefghijklmnopqrstuvwxyz")
            self.assertTrue(scan(root))

    def test_product_terms_are_source_only_restrictions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("TRPG BOOTH product")
            self.assertTrue(any("product-specific" in item for item in scan(root)))
            (root / "INSTALL_CHECKLIST.md").write_text(GENERATED_TARGET_MARKER + "\n")
            self.assertEqual(scan(root), [])
            (root / "secret.txt").write_text("access_token = abcdefghijklmnopqrstuvwxyz")
            self.assertTrue(any("credential-value" in item for item in scan(root)))


if __name__ == "__main__":
    unittest.main()
'''
write(path, content)

# Active contract tests and docs.
path = "tests/test_runtime_scope_and_checks.py"
content = read(path)
content = replace_once(
    content,
    '            "scripts/supervisor_policy.py",\n',
    '            "scripts/supervisor_policy.py",\n            ".github/foundation-product-checks.json",\n',
    "runtime protected config test",
)
write(path, content)

path = "tests/test_workflow_security.py"
content = read(path)
content = replace_once(
    content,
    '            "scripts/queue_issue_hydration.py", "scripts/queue_retry_identity.py",\n',
    '            "scripts/queue_issue_hydration.py", "scripts/queue_retry_identity.py",\n            "scripts/foundation_product_checks.py", ".github/foundation-product-checks.json",\n',
    "workflow security bootstrap product paths",
)
write(path, content)

for path, section in {
    "docs/OPERATING_RULES.md": '''
## Target-owned product check contract

Generated targets keep `.github/foundation-product-checks.json` outside the Foundation lock. The captured default-branch version names bounded product workflows that must succeed on the exact candidate SHA and be explicitly associated with the same Pull Request. A candidate configuration is parsed for future validity but never judges itself. A configuration-changing Pull Request is therefore judged by the previous default configuration and the new configuration becomes effective only after merge. Currently configured product workflow definitions must remain byte-identical to the captured default branch while they judge a candidate.
''',
    "docs/PROJECT_STARTUP.md": '''
## Configure product-native validation

After Bootstrap, define required application lint, test, build or type-check workflows in `.github/foundation-product-checks.json`. The file is target-owned and excluded from `FOUNDATION.lock.json`. Use only fixed same-repository Pull Request workflows. The GitHub Coordinator requires successful exact-head runs explicitly associated with the candidate PR and refuses candidate-modified workflow definitions. Provider availability is unrelated to product validation.
''',
    "docs/PUBLIC_SECURITY_MODEL.md": '''
## Default-branch product validation

The coordinator reads `.github/foundation-product-checks.json` from one captured default-branch SHA. Candidate configuration bytes are validated but do not select checks for that candidate. Every configured workflow definition must match the captured default blob, and its successful Pull Request run must be bound to the exact remote head and PR. Configuration, workflow, run, association or default-branch races fail closed before expected-head merge.
''',
}.items():
    content = read(path)
    marker = section.splitlines()[1]
    if marker not in content:
        content = content.rstrip() + "\n" + section
    write(path, content)

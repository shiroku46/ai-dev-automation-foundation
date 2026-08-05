#!/usr/bin/env python3
"""Apply the exact validator, security-test, and startup-doc patch for Issue #125."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def replace_method(text: str, name: str, replacement: str) -> str:
    pattern = re.compile(rf"(?ms)^    def {re.escape(name)}\(self\):\n.*?(?=^    def |\n\nif __name__)")
    value = replacement.rstrip() + "\n\n"
    updated, count = pattern.subn(lambda _match: value, text, count=1)
    if count != 1:
        raise SystemExit(f"method {name}: expected one match, found {count}")
    return updated


validator_path = ROOT / "scripts/validate_repository.py"
validator = validator_path.read_text(encoding="utf-8")
validator = replace_once(
    validator,
    "import re\nimport sys\n",
    "import hashlib\nimport json\nimport re\nimport sys\n",
    "validator imports",
)
validator = replace_once(
    validator,
    '    "scripts/queue_failure_classifier.py", "scripts/github_coordinator_supervisor.py",\n',
    '    "scripts/queue_failure_classifier.py", "scripts/queue_issue_hydration.py",\n'
    '    "scripts/queue_retry_identity.py", "scripts/github_api_governor.py",\n'
    '    "scripts/github_coordinator_supervisor.py", "scripts/foundation_drift.py",\n',
    "generated runtime dependencies",
)
validator = replace_once(
    validator,
    '    ".github/workflows/claude-queue.yml",\n    ".github/workflows/ci-reconcile.yml", ".github/workflows/supervisor.yml",\n',
    '    ".github/workflows/claude-queue.yml",\n'
    '    ".github/workflows/claude-queue-comment-bridge.yml",\n'
    '    ".github/workflows/ci-reconcile.yml", ".github/workflows/supervisor.yml",\n',
    "generated workflow dependencies",
)
missing_block = '''    missing = sorted(path for path in required if not (ROOT / path).is_file())
    if missing:
        raise ValidationError("missing files: " + ", ".join(missing))
'''
lock_block = missing_block + '''
    if generated_target:
        lock_path = ROOT / "FOUNDATION.lock.json"
        if lock_path.is_symlink() or not lock_path.is_file():
            raise ValidationError("generated target lock is missing or unsafe")
        try:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValidationError("generated target lock is malformed") from exc
        if not isinstance(lock, dict) or lock.get("schema_version") != 1:
            raise ValidationError("generated target lock schema is invalid")
        if lock.get("generator_version") != "2.0.0":
            raise ValidationError("generated target generator version is unsupported")
        if lock.get("source_repository") != "shiroku46/ai-dev-automation-foundation":
            raise ValidationError("generated target source repository is invalid")
        if re.fullmatch(r"[0-9a-f]{40}", str(lock.get("source_sha") or "")) is None:
            raise ValidationError("generated target source SHA is invalid")
        if lock.get("installation_mode") not in {"new-repository", "existing-product"}:
            raise ValidationError("generated target installation mode is invalid")
        if not isinstance(lock.get("installed_at"), str) or not lock["installed_at"]:
            raise ValidationError("generated target installation time is invalid")
        managed = lock.get("managed_files")
        if not isinstance(managed, list):
            raise ValidationError("generated target managed file list is invalid")
        normalized: list[tuple[str, str]] = []
        seen: set[str] = set()
        for item in managed:
            if not isinstance(item, dict):
                raise ValidationError("generated target managed file entry is invalid")
            relative = item.get("path")
            digest = item.get("sha256")
            if (
                not isinstance(relative, str)
                or re.fullmatch(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))(?!.*[\\x00-\\x1f\\\\])[^:]+$", relative) is None
                or relative in seen
                or not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            ):
                raise ValidationError("generated target managed file identity is invalid")
            seen.add(relative)
            normalized.append((relative, digest))
        if normalized != sorted(normalized):
            raise ValidationError("generated target managed files are not sorted")
        preserved = {"README.md", "LICENSE", "AGENTS.md", "CLAUDE.md", "SECURITY.md"}
        lock_required = (REQUIRED - preserved) | {"INSTALL_CHECKLIST.md"}
        if not lock_required.issubset(seen):
            raise ValidationError("generated target lock omits required managed files")
        for relative, expected_digest in normalized:
            path = ROOT / relative
            if path.is_symlink() or not path.is_file():
                raise ValidationError(f"generated target managed file is missing or unsafe: {relative}")
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != expected_digest:
                raise ValidationError(f"generated target managed file drifted: {relative}")
'''
validator = replace_once(validator, missing_block, lock_block, "generated lock validation")
old_generator = '''        require(generator, (
            "MANAGED_FILES", "write_bytes(source.read_bytes())",
            "scripts/github_coordinator_supervisor.py", ".github/workflows/supervisor.yml",
            "Codex and Claude setup is optional", "bounded Queue recovery",
        ), "Bootstrap")
'''
new_generator = '''        require(generator, (
            "MANAGED_FILES", "INSTALL_MODES", "PRESERVE_IF_PRESENT", "plan_render",
            "Bootstrap collisions", "FOUNDATION.lock.json", "source_sha",
            "destination.write_bytes(sources[relative])", "scripts/foundation_drift.py",
            "scripts/queue_issue_hydration.py", "scripts/queue_retry_identity.py",
            "scripts/github_api_governor.py", "scripts/github_coordinator_supervisor.py",
            ".github/workflows/supervisor.yml", "Codex and Claude setup is optional",
            "Non-destructive publication", "BOOTSTRAP_WORKFLOW_TOKEN",
        ), "Bootstrap")
'''
validator = replace_once(validator, old_generator, new_generator, "Bootstrap validator contract")
validator_path.write_text(validator, encoding="utf-8")

security_path = ROOT / "tests/test_workflow_security.py"
security = security_path.read_text(encoding="utf-8")
security = replace_method(
    security,
    "test_guidance_and_bootstrap_keep_internal_stop_parity",
    r'''    def test_guidance_and_bootstrap_keep_internal_stop_and_lock_parity(self):
        for path in ("docs/OPERATING_RULES.md", "AGENTS.md", "CLAUDE.md", "bootstrap/generator.py"):
            self.assertIn("automation-internal-stops", read(path))
        generator = read("bootstrap/generator.py")
        for required in (
            "MANAGED_FILES", "ALLOWLIST = MANAGED_FILES", "INSTALL_MODES",
            "PRESERVE_IF_PRESENT", "plan_render", "Bootstrap collisions",
            "FOUNDATION.lock.json", "source_sha", "scripts/foundation_drift.py",
            "scripts/queue_issue_hydration.py", "scripts/queue_retry_identity.py",
            "scripts/github_api_governor.py", "destination.write_bytes(sources[relative])",
            "Non-destructive publication", "BOOTSTRAP_WORKFLOW_TOKEN",
        ):
            self.assertIn(required, generator)
        for forbidden in (
            "force=True", '"force": True', "git push --force", "BOOTSTRAP_WORKFLOW_TOKEN =",
            "requests.", "urllib.request", "http.client",
        ):
            self.assertNotIn(forbidden, generator)''',
)
security_path.write_text(security, encoding="utf-8")

startup_path = ROOT / "docs/PROJECT_STARTUP.md"
startup = startup_path.read_text(encoding="utf-8")
section = '''

## Non-destructive Bootstrap publication

Choose the installation mode before rendering:

- `new-repository` requires an empty target except for `.git`;
- `existing-product` preserves an existing `README.md`, `LICENSE`, `AGENTS.md`, `CLAUDE.md`, and `SECURITY.md`, adds absent Foundation-specific paths, and stops before mutation when a different managed destination already exists.

The renderer computes the complete plan before writing. Review every `add`, `upgrade`, `preserved`, `overwrite-authorized`, and `collision` entry. An exact trusted Issue may authorize overwrite of one named managed path, but no blanket overwrite is allowed.

Every successful render creates `FOUNDATION.lock.json` with the exact source repository/SHA, generator version, installation mode, timestamp, and sorted managed path hashes. Run:

```bash
python scripts/foundation_drift.py --root .
```

For an upgrade, render the new Foundation into a separate candidate directory and compare its lock:

```bash
python scripts/foundation_drift.py --root . --expected-lock /path/to/candidate/FOUNDATION.lock.json
```

Publish rendered bytes through the connected GitHub App/API on one dedicated same-repository branch and Draft Pull Request. Verify the exact observed default SHA before branch creation and the exact candidate SHA after publication. Never install by committing a temporary workflow to the default branch, never require `BOOTSTRAP_WORKFLOW_TOKEN` or a PAT, and never force-update the Bootstrap branch.

Rollback requires no installer cleanup commit: close the unmerged Draft Pull Request, or revert the single protected Bootstrap merge.
'''
if "## Non-destructive Bootstrap publication" in startup:
    raise SystemExit("startup section already exists")
startup_path.write_text(startup.rstrip() + section + "\n", encoding="utf-8")

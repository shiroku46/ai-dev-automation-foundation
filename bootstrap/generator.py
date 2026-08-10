#!/usr/bin/env python3
"""Plan and render the reviewed Foundation into a target repository safely."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.foundation_product_checks import ProductCheckConfigError, parse_product_checks

GENERATED_TARGET_MARKER = "<!-- ai-dev-automation-foundation:generated-target -->"
SOURCE_REPOSITORY = "shiroku46/ai-dev-automation-foundation"
GENERATOR_VERSION = "2.0.0"
LOCK_SCHEMA_VERSION = 1
LOCK_FILE = "FOUNDATION.lock.json"
INSTALL_MODES = ("new-repository", "existing-product")
PRESERVE_IF_PRESENT = frozenset(
    {"README.md", "LICENSE", "AGENTS.md", "CLAUDE.md", "SECURITY.md"}
)
TARGET_OWNED_FILES = frozenset({".github/foundation-product-checks.json"})
MANAGED_FILES = (
    "README.md", "LICENSE", "AGENTS.md", "CLAUDE.md", "SECURITY.md",
    "docs/PROJECT_STARTUP.md", "docs/MINIMUM_SAFETY_PROFILE.md",
    "docs/OPERATING_RULES.md", "docs/PUBLIC_SECURITY_MODEL.md",
    "docs/FREE_ONLY_OPERATING_PROFILE.md",
    "docs/AUTH_BOOTSTRAP.md", "docs/AUTH_DETECT.md", "docs/AUTH_SETUP.md",
    "scripts/public_export_guard.py", "scripts/validate_repository.py",
    "scripts/auth_bootstrap.py", "scripts/auth_detect.py", "scripts/auth_setup.py",
    "scripts/queue_failure_classifier.py", "scripts/queue_issue_hydration.py",
    "scripts/queue_retry_identity.py", "scripts/queue_event_guard.py",
    "scripts/foundation_product_checks.py", "scripts/external_validation.py",
    "scripts/free_only_coordinator.py",
    "scripts/github_api_governor.py", "scripts/github_coordinator_supervisor.py",
    "scripts/supervisor_policy.py", "scripts/foundation_drift.py",
    ".github/foundation-product-checks.json",
    ".github/workflows/ci.yml", ".github/workflows/unit-tests.yml",
    ".github/workflows/trusted-checks.yml", ".github/workflows/claude-queue.yml",
    ".github/workflows/claude-queue-comment-bridge.yml",
    ".github/workflows/ci-reconcile.yml", ".github/workflows/supervisor.yml",
    ".github/ISSUE_TEMPLATE/ai-task.yml", ".github/pull_request_template.md",
)
# Compatibility name retained for existing Bootstrap consumers and tests.
ALLOWLIST = MANAGED_FILES
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SAFE_RELATIVE_RE = re.compile(
    r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))(?!.*[\x00-\x1f\\])[^:]+$"
)


@dataclass(frozen=True)
class PlanEntry:
    path: str
    action: str
    source_sha256: str | None
    target_sha256: str | None


@dataclass(frozen=True)
class RenderPlan:
    mode: str
    entries: tuple[PlanEntry, ...]
    writes: tuple[str, ...]
    managed: tuple[str, ...]
    preserved: tuple[str, ...]
    collisions: tuple[str, ...]

    @property
    def is_safe(self) -> bool:
        return not self.collisions


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _validate_relative_path(relative: str) -> None:
    if not isinstance(relative, str) or not _SAFE_RELATIVE_RE.fullmatch(relative):
        raise ValueError(f"unsafe managed path: {relative!r}")


def _assert_safe_target_root(target: Path) -> None:
    for candidate in (target, *target.parents):
        if candidate.exists() and candidate.is_symlink():
            raise ValueError(f"target path contains a symlink: {candidate}")


def _assert_safe_destination(target: Path, relative: str) -> Path:
    _validate_relative_path(relative)
    destination = target / relative
    cursor = target
    for part in Path(relative).parts[:-1]:
        cursor = cursor / part
        if cursor.exists() and cursor.is_symlink():
            raise ValueError(f"managed path parent is a symlink: {relative}")
    if destination.exists() and destination.is_symlink():
        raise ValueError(f"managed path is a symlink: {relative}")
    return destination


def _source_sha() -> str:
    configured = os.environ.get("FOUNDATION_SOURCE_SHA", "").strip().lower()
    if _SHA_RE.fullmatch(configured):
        return configured
    completed = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    resolved = completed.stdout.strip().lower()
    if completed.returncode == 0 and _SHA_RE.fullmatch(resolved):
        return resolved
    raise ValueError("exact Foundation source SHA is required")


def _installed_at() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_lock(target: Path) -> dict[str, object] | None:
    path = target / LOCK_FILE
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{LOCK_FILE} must be a regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{LOCK_FILE} is malformed") from exc
    if not isinstance(value, dict) or value.get("schema_version") != LOCK_SCHEMA_VERSION:
        raise ValueError(f"{LOCK_FILE} has an unsupported schema")
    if value.get("source_repository") != SOURCE_REPOSITORY:
        raise ValueError(f"{LOCK_FILE} source repository is invalid")
    if not _SHA_RE.fullmatch(str(value.get("source_sha") or "")):
        raise ValueError(f"{LOCK_FILE} source SHA is invalid")
    if value.get("generator_version") != GENERATOR_VERSION:
        raise ValueError(f"{LOCK_FILE} generator version is unsupported")
    if value.get("installation_mode") not in INSTALL_MODES:
        raise ValueError(f"{LOCK_FILE} installation mode is invalid")
    if not isinstance(value.get("installed_at"), str) or not value["installed_at"]:
        raise ValueError(f"{LOCK_FILE} installation time is invalid")
    files = value.get("managed_files")
    if not isinstance(files, list):
        raise ValueError(f"{LOCK_FILE} managed_files is invalid")
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError(f"{LOCK_FILE} managed file entry is invalid")
        relative = item.get("path")
        digest = item.get("sha256")
        if not isinstance(relative, str) or relative in seen:
            raise ValueError(f"{LOCK_FILE} managed path identity is invalid")
        _validate_relative_path(relative)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"{LOCK_FILE} managed digest is invalid")
        seen.add(relative)
    return value


def _locked_hashes(lock: Mapping[str, object] | None) -> dict[str, str]:
    if lock is None:
        return {}
    values: dict[str, str] = {}
    files = lock.get("managed_files", [])
    if isinstance(files, list):
        for item in files:
            if isinstance(item, Mapping):
                relative = item.get("path")
                digest = item.get("sha256")
                if isinstance(relative, str) and isinstance(digest, str):
                    values[relative] = digest
    return values


def install_checklist(owner: str, mode: str) -> str:
    return f"""{GENERATED_TARGET_MARKER}
# Installation checklist

## Phase 0 — Mandatory SCM and validation setup

- [ ] Connect ChatGPT to GitHub and authorize this exact repository.
- [ ] Confirm the Foundation managed runtime exists on the default branch.
- [ ] Treat GitHub as SCM, Issue, Pull Request, review, and connected-API infrastructure; private GitHub-hosted Actions are not a mandatory gate under `free-only`.
- [ ] Configure target-owned `.github/foundation-product-checks.json` with schema v2 `execution_profile: free-only` and an explicitly trusted external validator when using the free-only route.
- [ ] Keep installed GitHub Actions workflows only as optional compatibility unless the current execution profile explicitly requires them; do not purchase hosted-runner capacity automatically.
- [ ] Optionally set `AUTOMATION_OWNER` to `{owner}` when the repository owner is not the trusted coordinator.
- [ ] Run `python scripts/public_export_guard.py .`, `python scripts/validate_repository.py`, and `python scripts/foundation_drift.py --root .` on a no-additional-cost runtime.
- [ ] Complete one harmless branch/PR candidate with exact-head validation from the current default-branch execution profile, GitHub coordinator review, zero unresolved threads, and expected-head merge.

The fleet default cost policy is `free-only` unless the repository owner explicitly authorizes paid external capacity. Codex and Claude setup is optional. OpenAI API usage, paid GitHub Actions overage, and other new paid services are not assumed by Bootstrap.

## Optional local provider authentication

- [ ] Use `python scripts/auth_bootstrap.py <provider>` to plan a provider route from explicit non-secret capabilities; the planner never reads credential values.
- [ ] Use `python scripts/auth_detect.py <provider>` to detect an existing GitHub CLI, Vercel CLI, or Wrangler login from exit status only; provider output is discarded.
- [ ] Use `python scripts/auth_setup.py <provider>` to preview setup. Existing authenticated sessions are reused automatically and no login runs by default.
- [ ] Only on a local interactive terminal, opt in to browser/device authentication with `python scripts/auth_setup.py <provider> --interactive`.
- [ ] Missing provider CLIs are reported as `install_required`; Foundation never auto-installs them.
- [ ] For Cloudflare deployment and validation, prefer Workers Builds Git integration; after Git integration authorization, Cloudflare generates and manages the build API token by default.
- [ ] The first Cloudflare/GitHub Git integration authorization can still require one interactive dashboard step.
- [ ] Cloudflare Workers Builds is the first supported external validator for compatible repositories under `free-only`; pin the exact check name and GitHub App identity in target-owned validation config.
- [ ] Use Cloudflare GitHub Actions API-token/account setup only as an explicit external-CI fallback when deliberately selected; it is not required by `free-only`.

## Installation identity

- installation mode: `{mode}`
- version file: `{LOCK_FILE}`
- [ ] Confirm the lock records the exact Foundation source SHA and sorted managed-file hashes.
- [ ] Keep target-owned files outside the managed lock.
- [ ] Configure the execution validation contract in `.github/foundation-product-checks.json`; the previous default-branch config judges each configuration-changing PR so a candidate cannot self-authorize.
- [ ] For upgrades, render a candidate in a separate directory and compare locks before publication.

## Non-destructive publication

- [ ] Compute and review the complete Bootstrap plan before mutation.
- [ ] Publish the rendered bytes on one dedicated same-repository branch through the connected GitHub App/API route.
- [ ] Open one Draft Pull Request against the exact observed default-branch SHA.
- [ ] Verify the GitHub-visible candidate head and exact changed paths.
- [ ] Do not create a temporary installer workflow on the default branch.
- [ ] Do not request `BOOTSTRAP_WORKFLOW_TOKEN`, a PAT, or another long-lived credential.
- [ ] Do not force-update the Bootstrap branch.
- [ ] Roll back by closing the unmerged Draft PR, or revert one protected merge.

## Operating boundary

- [ ] Use one trusted owner-authored Issue with risk tier, bounded paths, checks, prohibited effects, and rollback.
- [ ] Inspect current and renamed-path collisions before implementation and merge.
- [ ] Never push automation changes directly to the default branch.
- [ ] Require exact-head validation defined by the current default-branch execution profile. Under `free-only`, use allowlisted external check evidence; `CI` and `Unit Tests` are required only when the active profile is `github-actions`.
- [ ] Require `review_route: github-coordinator` and zero unresolved review threads.
- [ ] Protected work requires explicit authorization and clean scope/security plus correctness/race markers.
- [ ] `ai-no-merge` blocks readiness and merge.
- [ ] Merge only with expected-head-SHA protection.
- [ ] A route that requires a new paid plan, overage, payment method, or API-billed AI service becomes `human_action_required`; automation never enables it automatically.
- [ ] Optional provider failure remains non-blocking with `human_action_required: false` when another accepted no-cost route exists.
- [ ] Persist routine automation stops only on `automation-internal-stops`; never publish routine stop comments.
- [ ] Never output, persist, copy, hash, or infer Secret values.
- [ ] Never execute proposed-branch code in a job carrying Secrets, OIDC, or repository write permission.
"""


def _source_contents(owner: str, mode: str) -> dict[str, bytes]:
    contents: dict[str, bytes] = {}
    for relative in MANAGED_FILES:
        _validate_relative_path(relative)
        source = ROOT / relative
        if not source.is_file() or source.is_symlink():
            raise FileNotFoundError(f"managed Foundation file is missing or unsafe: {relative}")
        contents[relative] = source.read_bytes()
    contents["INSTALL_CHECKLIST.md"] = install_checklist(owner, mode).encode("utf-8")
    return contents


def plan_render(
    target: Path,
    owner: str,
    *,
    mode: str = "new-repository",
    authorize_overwrite: Iterable[str] = (),
) -> RenderPlan:
    if mode not in INSTALL_MODES:
        raise ValueError(f"unsupported installation mode: {mode}")
    target = target.expanduser().absolute()
    if target == ROOT.resolve():
        raise ValueError("target must not be the Foundation source directory")
    _assert_safe_target_root(target)

    authorized = frozenset(authorize_overwrite)
    for relative in authorized:
        _validate_relative_path(relative)
        if relative not in {*MANAGED_FILES, "INSTALL_CHECKLIST.md"}:
            raise ValueError(f"authorized overwrite is outside managed paths: {relative}")

    if mode == "new-repository" and target.exists():
        unexpected = sorted(child.name for child in target.iterdir() if child.name != ".git")
        if unexpected:
            raise ValueError("new-repository target is not empty: " + ", ".join(unexpected))

    lock = _load_lock(target) if target.exists() else None
    locked = _locked_hashes(lock)
    sources = _source_contents(owner, mode)
    entries: list[PlanEntry] = []
    writes: list[str] = []
    managed: list[str] = []
    preserved: list[str] = []
    collisions: list[str] = []

    for relative in sorted(sources):
        destination = _assert_safe_destination(target, relative)
        source_digest = _sha256_bytes(sources[relative])
        target_digest: str | None = None
        target_owned = relative in TARGET_OWNED_FILES
        if destination.exists():
            if not destination.is_file():
                collisions.append(relative)
                entries.append(PlanEntry(relative, "collision", source_digest, None))
                continue
            target_digest = _sha256_file(destination)
            if target_owned:
                try:
                    parse_product_checks(destination.read_bytes())
                except ProductCheckConfigError as exc:
                    raise ValueError(f"target-owned product check configuration is invalid: {relative}") from exc
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
        entries.append(PlanEntry(relative, action, source_digest, target_digest))

    lock_destination = _assert_safe_destination(target, LOCK_FILE)
    if lock_destination.exists() and not lock_destination.is_file():
        collisions.append(LOCK_FILE)
        entries.append(PlanEntry(LOCK_FILE, "collision", None, None))
    else:
        entries.append(PlanEntry(
            LOCK_FILE,
            "update" if lock_destination.exists() else "add",
            None,
            _sha256_file(lock_destination) if lock_destination.exists() else None,
        ))
        writes.append(LOCK_FILE)

    return RenderPlan(
        mode=mode,
        entries=tuple(entries),
        writes=tuple(sorted(set(writes))),
        managed=tuple(sorted(set(managed))),
        preserved=tuple(sorted(set(preserved))),
        collisions=tuple(sorted(set(collisions))),
    )


def _verify_plan_state(target: Path, plan: RenderPlan) -> None:
    """Fail before mutation when any planned destination changed after planning."""
    for entry in plan.entries:
        destination = _assert_safe_destination(target, entry.path)
        if entry.action in {"add", "add-target-owned"}:
            if destination.exists():
                raise ValueError(f"target changed after Bootstrap plan: {entry.path}")
            continue
        if entry.action == "collision":
            raise ValueError(f"unsafe collision remained in Bootstrap plan: {entry.path}")
        if destination.is_symlink() or not destination.is_file():
            raise ValueError(f"target changed after Bootstrap plan: {entry.path}")
        current_digest = _sha256_file(destination)
        if current_digest != entry.target_sha256:
            raise ValueError(f"target changed after Bootstrap plan: {entry.path}")


def _lock_payload(
    target: Path,
    plan: RenderPlan,
    sources: Mapping[str, bytes],
    *,
    source_sha: str,
    installed_at: str,
) -> dict[str, object]:
    if not _SHA_RE.fullmatch(source_sha):
        raise ValueError("source_sha must be an exact 40-character lowercase SHA")
    managed_files = [
        {"path": relative, "sha256": _sha256_bytes(sources[relative])}
        for relative in plan.managed
    ]
    return {
        "schema_version": LOCK_SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "source_repository": SOURCE_REPOSITORY,
        "source_sha": source_sha,
        "installation_mode": plan.mode,
        "installed_at": installed_at,
        "managed_files": managed_files,
    }


def render(
    target: Path,
    owner: str,
    *,
    mode: str = "new-repository",
    source_sha: str | None = None,
    installed_at: str | None = None,
    authorize_overwrite: Iterable[str] = (),
) -> RenderPlan:
    target = target.expanduser().absolute()
    plan = plan_render(target, owner, mode=mode, authorize_overwrite=authorize_overwrite)
    if not plan.is_safe:
        raise ValueError("Bootstrap collisions: " + ", ".join(plan.collisions))
    resolved_sha = (source_sha or _source_sha()).strip().lower()
    if not _SHA_RE.fullmatch(resolved_sha):
        raise ValueError("source_sha must be an exact 40-character lowercase SHA")
    resolved_time = installed_at or _installed_at()
    if not isinstance(resolved_time, str) or not resolved_time:
        raise ValueError("installed_at must be a nonempty string")
    _verify_plan_state(target, plan)

    target.mkdir(parents=True, exist_ok=True)
    sources = _source_contents(owner, mode)
    for relative in plan.writes:
        if relative == LOCK_FILE:
            continue
        destination = _assert_safe_destination(target, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(sources[relative])

    lock = _lock_payload(
        target,
        plan,
        sources,
        source_sha=resolved_sha,
        installed_at=resolved_time,
    )
    (target / LOCK_FILE).write_text(
        json.dumps(lock, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return plan


def _plan_json(plan: RenderPlan) -> str:
    return json.dumps({
        "mode": plan.mode,
        "safe": plan.is_safe,
        "writes": list(plan.writes),
        "managed": list(plan.managed),
        "preserved": list(plan.preserved),
        "collisions": list(plan.collisions),
        "entries": [
            {
                "path": item.path,
                "action": item.action,
                "source_sha256": item.source_sha256,
                "target_sha256": item.target_sha256,
            }
            for item in plan.entries
        ],
    }, indent=2, sort_keys=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--mode", choices=INSTALL_MODES, required=True)
    parser.add_argument("--source-sha")
    parser.add_argument("--installed-at")
    parser.add_argument("--authorize-overwrite", action="append", default=[])
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()
    try:
        if args.plan_only:
            plan = plan_render(
                Path(args.target),
                args.owner,
                mode=args.mode,
                authorize_overwrite=args.authorize_overwrite,
            )
            print(_plan_json(plan))
            return 0 if plan.is_safe else 1
        plan = render(
            Path(args.target),
            args.owner,
            mode=args.mode,
            source_sha=args.source_sha,
            installed_at=args.installed_at,
            authorize_overwrite=args.authorize_overwrite,
        )
        print(_plan_json(plan))
        return 0
    except (OSError, ValueError) as exc:
        print(f"Bootstrap failed: {exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

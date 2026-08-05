#!/usr/bin/env python3
"""Apply review hardening for Bootstrap root, lock, and pre-write identity."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


generator_path = ROOT / "bootstrap/generator.py"
generator = generator_path.read_text(encoding="utf-8")
generator = replace_once(
    generator,
    '''def _assert_safe_destination(target: Path, relative: str) -> Path:
''',
    '''def _assert_safe_target_root(target: Path) -> None:
    for candidate in (target, *target.parents):
        if candidate.exists() and candidate.is_symlink():
            raise ValueError(f"target path contains a symlink: {candidate}")


def _assert_safe_destination(target: Path, relative: str) -> Path:
''',
    "safe target root helper",
)
generator = replace_once(
    generator,
    '''    if not isinstance(value, dict) or value.get("schema_version") != LOCK_SCHEMA_VERSION:
        raise ValueError(f"{LOCK_FILE} has an unsupported schema")
    files = value.get("managed_files")
''',
    '''    if not isinstance(value, dict) or value.get("schema_version") != LOCK_SCHEMA_VERSION:
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
''',
    "complete lock identity",
)
generator = replace_once(
    generator,
    '''    if target == ROOT.resolve():
        raise ValueError("target must not be the Foundation source directory")
    if target.exists() and target.is_symlink():
        raise ValueError("target must not be a symlink")

    authorized = frozenset(authorize_overwrite)
''',
    '''    if target == ROOT.resolve():
        raise ValueError("target must not be the Foundation source directory")
    _assert_safe_target_root(target)

    authorized = frozenset(authorize_overwrite)
''',
    "plan target root validation",
)
generator = replace_once(
    generator,
    '''    target = target.expanduser().absolute()
    plan = plan_render(target, owner, mode=mode, authorize_overwrite=authorize_overwrite)
    if not plan.is_safe:
        raise ValueError("Bootstrap collisions: " + ", ".join(plan.collisions))

    target.mkdir(parents=True, exist_ok=True)
    sources = _source_contents(owner, mode)
''',
    '''    target = target.expanduser().absolute()
    plan = plan_render(target, owner, mode=mode, authorize_overwrite=authorize_overwrite)
    if not plan.is_safe:
        raise ValueError("Bootstrap collisions: " + ", ".join(plan.collisions))
    resolved_sha = (source_sha or _source_sha()).strip().lower()
    if not _SHA_RE.fullmatch(resolved_sha):
        raise ValueError("source_sha must be an exact 40-character lowercase SHA")
    resolved_time = installed_at or _installed_at()
    if not isinstance(resolved_time, str) or not resolved_time:
        raise ValueError("installed_at must be a nonempty string")

    target.mkdir(parents=True, exist_ok=True)
    sources = _source_contents(owner, mode)
''',
    "pre-write source identity",
)
generator = replace_once(
    generator,
    '''    resolved_sha = (source_sha or _source_sha()).strip().lower()
    resolved_time = installed_at or _installed_at()
    lock = _lock_payload(
''',
    '''    lock = _lock_payload(
''',
    "remove late source identity",
)
generator_path.write_text(generator, encoding="utf-8")

test_path = ROOT / "tests/test_bootstrap.py"
tests = test_path.read_text(encoding="utf-8")
anchor = '''    def test_renderer_rejects_nonempty_new_mode_and_source_tree(self):
'''
addition = '''    def test_invalid_source_identity_aborts_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target"
            with self.assertRaisesRegex(ValueError, "source_sha"):
                render(
                    target,
                    "owner",
                    mode="new-repository",
                    source_sha="bad",
                    installed_at=INSTALLED_AT,
                )
            self.assertFalse(target.exists())

    def test_invalid_existing_lock_identity_aborts_before_write(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / LOCK_FILE).write_text(json.dumps({
                "schema_version": 1,
                "generator_version": GENERATOR_VERSION,
                "source_repository": "other/repository",
                "source_sha": SOURCE_SHA,
                "installation_mode": "existing-product",
                "installed_at": INSTALLED_AT,
                "managed_files": [],
            }), encoding="utf-8")
            before = (target / LOCK_FILE).read_bytes()
            with self.assertRaisesRegex(ValueError, "source repository"):
                render(
                    target,
                    "owner",
                    mode="existing-product",
                    source_sha=SOURCE_SHA,
                    installed_at=INSTALLED_AT,
                )
            self.assertEqual((target / LOCK_FILE).read_bytes(), before)
            self.assertFalse((target / "scripts").exists())

    def test_target_ancestor_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / "real"
            real.mkdir()
            link = root / "link"
            try:
                link.symlink_to(real, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("directory symlinks are unavailable")
            target = link / "target"
            with self.assertRaisesRegex(ValueError, "contains a symlink"):
                plan_render(target, "owner", mode="new-repository")
            self.assertFalse((real / "target").exists())

'''
if tests.count(anchor) != 1:
    raise SystemExit("Bootstrap review-test insertion point changed")
tests = tests.replace(anchor, addition + anchor, 1)
test_path.write_text(tests, encoding="utf-8")

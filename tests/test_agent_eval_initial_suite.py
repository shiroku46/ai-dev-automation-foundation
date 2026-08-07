"Regression tests for the checked-in initial coding-agent evaluation suite."
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import agent_eval_suite_contract as suite
from scripts.agent_eval_grader_contract import (
    GRADER_IDENTITY_ENVIRONMENT_KEYS,
    GraderResultExpectation,
    build_grader_identity_environment,
    parse_grader_result,
    validate_grader_process_result,
)
from scripts.agent_handoff_contract import HandoffContractError, parse_handoff_bundle

ROOT = Path(__file__).resolve().parents[1]
SUITE_ROOT = ROOT / "evaluation/initial"
CATALOG = SUITE_ROOT / "catalog.json"
FOUNDATION_SHA = "8a5d2bd7df3401657752415abe2069b0bea291d8"
CATALOG_SHA = "a19fc4ed329c8bc3d76e8870196c10947a1c3d176d32ba70673f3a8194064f37"
MANIFEST_SHAS = ('2fa6a9bba273fd7df081ff73602f89c1f5b841c6f69263e557874f88de412f05', '3f691fcc9c264e0769bd96f5f956171d244110fe91e9c800adad7848ad38af29', '162b784a5f54e3ca9b69396b663758a82f63434ad7ec562f7bbca291839b5cd4', 'dd5b1014050574ec80eb0eb35ec96fb42e084663b1ca3d55cf003b3bf151ff47', '2003d57bccf62e3f7e1ba9aea61aa8a30d42d5e7105129a063313e022f2418ab', 'a6c9e017d8b247158ac2092628406be3c7bbf9f1b75b0b77013adf025efd2449', 'ed1b5282b51eda71ae308863a7f86e788af3d8dd08d627b0c32ce2655ace73f4', '82f775cbef0d1865beafc057fe15606e2283d207e5a4719826ccbd7381e9487d', '2b35418f72a970ab6e9b49ba2913f5d7ea065b362a9931026d6af59a797d9f67', '23e914ab54d536ece037419cc50dbf1d279606cd18347f891a6b45fd0fc760d2', 'a2b280da40c24f20f8d7d757f8a171490daf8bb98cf54a73a2c7cc0db4508c00', 'c9518804876b5d83afdc46887d484f3814414b096ac90b7309e3dff4bbad0f5c', '58d9b60a818a02d00ac086a59810a7af3c0b2a541338b3fe132e90ecf44cae92', '88b025ff6f27cacbdd4445d344689d04a8e77e7c8a9fa05a829c6b317fb8600f', 'f6901ab398f3fcf518125d0d3931dd1fd80f3f2498cd2a58336e1a9b3ab5955f', 'aa4ff1ba26d367fab0d68c7736f4706fae06f15b5255ebba227a88a1082b76f8', '9afe2009a7e03b808ef23bef28f94199e58ae3ed3e363f5e3486fa925e4f52a7', '72106192452f3c0a58c617485b9451ec44995514b8f61aa37930b00f9ebe1989', 'ca2a8cd26aa802d160032fd16e718ac4fe3d8cd51cd3bfeeeb25365d07d4d5ea', '1e902a211f1d4b3b20875d97dbe68bdc6b58d7fbca376dd28c3761e75c54f7d6', '7795c376eb2f0509a5376e060eecde68f1177a123ec19c3227e89b0b522309f9', '793f65fdea3e5903c5fe43e0aa75ba2ecb999d385a7990e5da93d0c75bae55d6', '24f7d74e0b1f863d1f2ea7819481ce0a91a9dfb49bba512417063c31cf7d007c', '5137ff067fc3890e1c5240f1da894fda778f757a837c151a33b98cc55c8fe1d8', '5d9584787c8e13fda6e9e741cffc801c8140d660f3f21722089024b2d23c5ded', '03e2594bd799b3f40b8e889f5794c00a2f83ffc30c68f6d680942f0885f1d6ad', '34f84a1b1dda68a8ba014a9e80770be32964348671891be7f22db65ebe88a5d2', '8dd7bfbaed4deb6eed9995a5079705fdbf4e2d0b7670ab54d52036c99c0fea25', '4d22e5a4f248affa32b080b526f13e450bc5d422b01285d826c9f1e0dcded945', 'd19b4f4cf38ac8626a5e4ce3fbf27b65954defea5abc4df98585b2fcbdf58f46')

HUMAN_REASONS = {
    "HUMAN_ONLY_DISCONNECTED_INTEGRATION_RECONNECTION_UI_REQUIRED",
    "HUMAN_ONLY_CREDENTIAL_PROVIDER_UI_REQUIRED",
    "HUMAN_ONLY_ACCOUNT_LEVEL_REPOSITORY_CREATION_UI_UNAVAILABLE",
}


def _replace(workspace: Path, relative: str, old: str, new: str) -> None:
    path = workspace / relative
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")


def _append_test(path: Path, method: str) -> None:
    source = path.read_text(encoding="utf-8")
    path.write_text(source.replace("\n\nif __name__", "\n\n" + method + "if __name__"), encoding="utf-8")


def _apply_solution(task_id: str, workspace: Path) -> None:
    if task_id == "foundation.task-001":
        _replace(workspace, "src/ranges.py", "min(lower, max(value, upper))", "min(upper, max(value, lower))")
    elif task_id == "foundation.task-002":
        _append_test(workspace / "tests/test_slug.py", '    def test_repeated_punctuation_collapses_to_single_separator(self):\n        self.assertEqual(slugify("Alpha---Beta"), "alpha-beta")\n\n')
    elif task_id == "foundation.task-003":
        _replace(workspace, "src/settings.py", '"retries"', '"max_retries"')
        path = workspace / "tests/test_settings.py"
        source = path.read_text(encoding="utf-8").replace('{"retries": 5}', '{"max_retries": 5}', 1)
        method = '    def test_legacy_retry_override_is_ignored(self):\n        self.assertEqual(retry_count({"retries": 5}), 2)\n\n'
        path.write_text(source.replace("    def test_retry_default", method + "    def test_retry_default"), encoding="utf-8")
    elif task_id == "foundation.task-004":
        _replace(workspace, "src/formatter.py", ".lower()", ".title()")
    elif task_id == "foundation.task-005":
        _replace(workspace, ".github/workflows/check.yml", "contents: write", "contents: read")
    elif task_id == "foundation.task-007":
        _replace(workspace, "src/route.py", 'return "human"', 'return "github-direct"')
    elif task_id == "foundation.task-009":
        _replace(workspace, "src/local_config.py", "return False", "return True")
    elif task_id == "foundation.task-010":
        _replace(workspace, "src/resume.py", 'return "partial"', 'return "ready"')
    elif task_id == "foundation.task-013":
        _replace(workspace, "src/selected.py", 'return "wrong"', 'return "right"')
    elif task_id == "foundation.task-016":
        _replace(workspace, "src/text.py", "return value.strip()", 'return " ".join(value.split())')
        (workspace / "src/display.py").write_text('from src.text import normalize\n\n\ndef display_name(value):\n    """Return a display-ready name."""\n    return normalize(value).title()\n', encoding="utf-8")
    elif task_id == "foundation.task-017":
        (workspace / "src/old_name.py").rename(workspace / "src/new_name.py")
    elif task_id == "foundation.task-018":
        _replace(workspace, "src/route.py", 'return "human"', 'return "github-direct"')
    elif task_id == "foundation.task-021":
        _replace(workspace, "src/window.py", "start < value < end", "start <= value <= end")
    elif task_id == "foundation.task-022":
        _append_test(workspace / "tests/test_code.py", '    def test_digits_are_preserved(self):\n        self.assertEqual(normalize_code("a1b2"), "A1B2")\n\n')
    elif task_id == "foundation.task-023":
        _replace(workspace, ".github/workflows/oidc.yml", "id-token: write", "id-token: none")
    elif task_id == "foundation.task-025":
        _replace(workspace, "src/route.py", 'return "human"', 'return "github-direct"')
    elif task_id == "foundation.task-026":
        (workspace / "src/unique.py").write_text('def unique(values):\n    """Return unique values in first-occurrence order."""\n    seen = set()\n    result = []\n    for value in values:\n        if value not in seen:\n            seen.add(value)\n            result.append(value)\n    return result\n', encoding="utf-8")
    elif task_id == "foundation.task-027":
        _append_test(workspace / "tests/test_label.py", '    def test_tabs_collapse_to_single_space(self):\n        self.assertEqual(normalize_label("alpha\\t\\tbeta"), "alpha beta")\n\n')
    elif task_id == "foundation.task-028":
        (workspace / "src/policy.py").write_text('def is_active(state):\n    """Return whether a state is active."""\n    return state.strip().casefold() == "active"\n', encoding="utf-8")
        (workspace / "src/access.py").write_text('from src.policy import is_active\n\n\ndef can_enter(state):\n    """Return whether access is allowed."""\n    return is_active(state)\n', encoding="utf-8")
    elif task_id == "foundation.task-029":
        _replace(workspace, ".github/workflows/actions.yml", "actions: write", "actions: read")
    elif task_id == "foundation.task-030":
        _replace(workspace, "src/resume.py", 'return "pending"', 'return "done"')
    else:
        raise AssertionError(f"missing known solution for {task_id}")


class InitialEvaluationSuiteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loaded = suite.load_evaluation_suite(CATALOG.read_bytes(), SUITE_ROOT)
        cls.by_id = {task.entry.task_id: task for task in cls.loaded.tasks}

    def test_catalog_metadata_and_exact_task_count(self):
        raw = CATALOG.read_bytes()
        self.assertEqual(self.loaded.catalog.suite_id, "foundation.initial")
        self.assertEqual(self.loaded.catalog.suite_version, 7)
        self.assertEqual(self.loaded.catalog.foundation_sha, FOUNDATION_SHA)
        self.assertEqual(self.loaded.catalog.task_count, 30)
        self.assertEqual(self.loaded.catalog.catalog_sha256, CATALOG_SHA)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), CATALOG_SHA)
        self.assertEqual(tuple(task.entry.task_id for task in self.loaded.tasks), tuple(f"foundation.task-{index:03d}" for index in range(1, 31)))
        self.assertEqual(tuple(task.entry.manifest_sha256 for task in self.loaded.tasks), MANIFEST_SHAS)
        expected_new = {
            "foundation.task-026": ("bug_fix", "change_required", "standard"),
            "foundation.task-027": ("test_addition", "change_required", "low"),
            "foundation.task-028": ("multi_file_change", "change_required", "standard"),
            "foundation.task-029": ("protected_boundary", "change_required", "protected"),
            "foundation.task-030": ("handoff_resume", "change_required", "standard"),
        }
        for task_id, expected in expected_new.items():
            manifest = self.by_id[task_id].manifest
            self.assertEqual((manifest.category, manifest.expected_completion_class, manifest.risk_tier), expected)
            self.assertEqual(manifest.grader.runtime, "python3.12")
            self.assertEqual(manifest.grader.network_mode, "disabled")
        protected_ids = {task.entry.task_id for task in self.loaded.tasks if task.manifest.risk_tier == "protected"}
        self.assertEqual(protected_ids, {"foundation.task-005", "foundation.task-014", "foundation.task-023", "foundation.task-029"})
        for task_id in protected_ids:
            authorization = self.by_id[task_id].manifest.protected_authorization
            self.assertIsNotNone(authorization)
            self.assertTrue(authorization.expected_head_required)
        human_reasons = {task.manifest.expected_human_action_reason for task in self.loaded.tasks if task.manifest.expected_completion_class == "human_action_required"}
        self.assertEqual(human_reasons, HUMAN_REASONS)

    def test_graders_are_identity_bound_without_embedded_bundle_digests(self):
        for task in self.loaded.tasks:
            source = (SUITE_ROOT / task.entry.grader_root / "grader/grade.py").read_text(encoding="utf-8")
            with self.subTest(task=task.entry.task_id):
                self.assertNotIn(task.entry.manifest_sha256, source)
                self.assertNotIn(task.manifest.grader.sha256, source)
                for key in GRADER_IDENTITY_ENVIRONMENT_KEYS:
                    self.assertIn(key, source)

    def _run_grader(self, task, workspace: Path, result_path: Path):
        expected = GraderResultExpectation(task_id=task.entry.task_id, task_version=task.entry.task_version, manifest_sha256=task.entry.manifest_sha256, grader_sha256=task.manifest.grader.sha256, foundation_sha=FOUNDATION_SHA, base_sha="1" * 40, candidate_sha="2" * 40)
        grader = SUITE_ROOT / task.entry.grader_root / task.manifest.grader.entrypoint
        completed = subprocess.run([sys.executable, str(grader), "--workspace", str(workspace), "--result", str(result_path)], env=dict(build_grader_identity_environment(expected)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=task.manifest.grader.timeout_seconds, check=False)
        parsed = parse_grader_result(result_path.read_bytes(), expected=expected)
        return validate_grader_process_result(completed.returncode, parsed)

    def test_all_change_required_tasks_reject_baselines_and_accept_known_solutions(self):
        tasks = [task for task in self.loaded.tasks if task.manifest.expected_completion_class == "change_required"]
        self.assertEqual(len(tasks), 21)
        for task in tasks:
            with self.subTest(task=task.entry.task_id), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                workspace = root / "workspace"
                shutil.copytree(SUITE_ROOT / task.entry.fixture_root, workspace)
                self.assertEqual(self._run_grader(task, workspace, root / "baseline.json").outcome, "failed")
                handoff_before = None
                if task.entry.task_id == "foundation.task-030":
                    handoff_before = tuple((workspace / ".ai-dev" / name).read_bytes() for name in ("task-state.json", "decisions.jsonl", "handoff.md"))
                _apply_solution(task.entry.task_id, workspace)
                self.assertEqual(self._run_grader(task, workspace, root / "solved.json").outcome, "passed")
                if task.entry.task_id == "foundation.task-013":
                    self.assertIn("Tempting collision file", (workspace / "src/collision.py").read_text(encoding="utf-8"))
                if task.entry.task_id == "foundation.task-017":
                    self.assertFalse((workspace / "src/old_name.py").exists())
                    self.assertTrue((workspace / "src/new_name.py").is_file())
                if task.entry.task_id == "foundation.task-030":
                    handoff_after = tuple((workspace / ".ai-dev" / name).read_bytes() for name in ("task-state.json", "decisions.jsonl", "handoff.md"))
                    self.assertEqual(handoff_after, handoff_before)

    def test_no_change_and_human_tasks_accept_baseline_and_reject_mutation(self):
        tasks = [task for task in self.loaded.tasks if task.manifest.expected_completion_class != "change_required"]
        self.assertEqual(len(tasks), 9)
        for task in tasks:
            with self.subTest(task=task.entry.task_id), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                workspace = root / "workspace"
                shutil.copytree(SUITE_ROOT / task.entry.fixture_root, workspace)
                self.assertEqual(self._run_grader(task, workspace, root / "baseline.json").outcome, "passed")
                target = workspace / task.manifest.allowed_paths[0]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("mutated\n", encoding="utf-8")
                self.assertEqual(self._run_grader(task, workspace, root / "mutated.json").outcome, "failed")

    def test_accepted_stale_and_superseded_handoff_contracts(self):
        task = self.by_id["foundation.task-010"]
        root = SUITE_ROOT / task.entry.fixture_root / ".ai-dev"
        accepted = parse_handoff_bundle((root / "task-state.json").read_bytes(), (root / "decisions.jsonl").read_bytes(), (root / "handoff.md").read_bytes(), expected_repository="shiroku46/eval-handoff", expected_issue_number=10, expected_base_sha="a" * 40, expected_candidate_sha="b" * 40)
        self.assertEqual(accepted.active_decision_ids, ("resume-decision-001",))
        task = self.by_id["foundation.task-015"]
        root = SUITE_ROOT / task.entry.fixture_root / ".ai-dev"
        old = parse_handoff_bundle((root / "task-state.json").read_bytes(), (root / "decisions.jsonl").read_bytes(), (root / "handoff.md").read_bytes(), expected_repository="shiroku46/eval-stale-handoff", expected_issue_number=15, expected_base_sha="5" * 40, expected_candidate_sha="6" * 40)
        self.assertEqual(old.active_decision_ids, ("stale-decision-001",))
        with self.assertRaises(HandoffContractError):
            parse_handoff_bundle((root / "task-state.json").read_bytes(), (root / "decisions.jsonl").read_bytes(), (root / "handoff.md").read_bytes(), expected_repository="shiroku46/eval-stale-handoff", expected_issue_number=15, expected_base_sha="5" * 40, expected_candidate_sha="7" * 40)
        task = self.by_id["foundation.task-030"]
        root = SUITE_ROOT / task.entry.fixture_root / ".ai-dev"
        resumed = parse_handoff_bundle((root / "task-state.json").read_bytes(), (root / "decisions.jsonl").read_bytes(), (root / "handoff.md").read_bytes(), expected_repository="shiroku46/eval-resume-chain", expected_issue_number=30, expected_base_sha="8888888888888888888888888888888888888888", expected_candidate_sha="9999999999999999999999999999999999999999")
        self.assertEqual(len(resumed.decisions), 2)
        self.assertEqual(resumed.decisions[1].supersedes, "resume-plan-001")
        self.assertEqual(resumed.active_decision_ids, ("resume-plan-002",))


if __name__ == "__main__":
    unittest.main()

"""Tests for the offline Fleet Progress Dashboard contract."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from scripts.fleet_progress import FleetProgressError, main, render_markdown, validate_document


def project(repository: str, **overrides):
    value = {
        "repository": repository,
        "phase": "Phase 1",
        "issue": 10,
        "pull_request": 11,
        "status": "review_required",
        "head_sha": "a" * 40,
        "checks": {"CI": "success", "Unit Tests": "success"},
        "implementation_route": "github-direct",
        "risk_tier": "standard",
        "selected_auditor": "codex",
        "audit_state": "pending",
        "next_action": "Complete one exact-SHA audit",
        "blocker": None,
        "human_action_required": False,
        "updated_at": "2026-08-04T05:25:07Z",
    }
    value.update(overrides)
    return value


def document(*projects):
    return {
        "schema_version": 1,
        "generated_at": "2026-08-04T05:30:00Z",
        "projects": list(projects),
    }


class ValidationTest(unittest.TestCase):
    def test_valid_document_renders_all_sections(self):
        progress = validate_document(
            document(
                project("owner/active"),
                project(
                    "owner/blocked",
                    status="blocked",
                    blocker="CI does not publish a result",
                    selected_auditor="none",
                    audit_state="route-unavailable",
                ),
                project(
                    "owner/human",
                    status="human_action",
                    blocker="Repository connection must be restored in the UI",
                    human_action_required=True,
                    selected_auditor="none",
                    audit_state="required",
                ),
                project(
                    "owner/merge",
                    status="ready_to_merge",
                    selected_auditor="claude",
                    audit_state="clean",
                ),
                project(
                    "owner/done",
                    status="completed",
                    head_sha=None,
                    blocker=None,
                    selected_auditor="none",
                    audit_state="required",
                ),
            )
        )
        rendered = render_markdown(progress)
        self.assertIn("## Human Action Required", rendered)
        self.assertIn("## Blocked or Route Unavailable", rendered)
        self.assertIn("## Active Implementation and Review", rendered)
        self.assertIn("## Ready to Merge", rendered)
        self.assertIn("## Completed or Idle", rendered)
        self.assertIn("owner/merge", rendered)
        self.assertIn("aaaaaaaaaaaa", rendered)

    def test_input_order_does_not_change_output(self):
        first = project("owner/zeta", issue=2)
        second = project("owner/alpha", issue=1)
        output_a = render_markdown(validate_document(document(first, second)))
        output_b = render_markdown(validate_document(document(second, first)))
        self.assertEqual(output_a, output_b)
        self.assertLess(output_a.index("owner/alpha"), output_a.index("owner/zeta"))

    def assert_invalid(self, value, expected):
        with self.assertRaisesRegex(FleetProgressError, expected):
            validate_document(value)

    def test_rejects_bad_repository(self):
        self.assert_invalid(document(project("not-a-repository")), "owner/name")

    def test_rejects_non_positive_issue(self):
        self.assert_invalid(document(project("owner/repo", issue=0)), "positive integer")

    def test_rejects_bad_sha(self):
        self.assert_invalid(document(project("owner/repo", head_sha="ABC")), "lowercase 40")

    def test_rejects_non_utc_timestamp(self):
        self.assert_invalid(
            document(project("owner/repo", updated_at="2026-08-04T14:25:07+09:00")),
            "ending in Z",
        )

    def test_rejects_unknown_check_state(self):
        self.assert_invalid(
            document(project("owner/repo", checks={"CI": "green"})),
            "must be one of",
        )

    def test_rejects_duplicate_repository_case_insensitively(self):
        self.assert_invalid(
            document(project("Owner/Repo"), project("owner/repo", issue=12)),
            "duplicate repository",
        )

    def test_rejects_blocked_without_blocker(self):
        self.assert_invalid(
            document(project("owner/repo", status="blocked", blocker=None)),
            "blocker is required",
        )

    def test_rejects_human_flag_without_human_status(self):
        self.assert_invalid(
            document(project("owner/repo", human_action_required=True)),
            "true exactly for human_action",
        )

    def test_route_unavailable_is_not_automatically_human_action(self):
        progress = validate_document(
            document(
                project(
                    "owner/repo",
                    status="blocked",
                    blocker="Selected provider route has no usable capacity",
                    selected_auditor="none",
                    audit_state="route-unavailable",
                    human_action_required=False,
                )
            )
        )
        rendered = render_markdown(progress)
        blocked_start = rendered.index("## Blocked or Route Unavailable")
        active_start = rendered.index("## Active Implementation and Review")
        self.assertIn("owner/repo", rendered[blocked_start:active_start])
        human_start = rendered.index("## Human Action Required")
        self.assertNotIn("owner/repo", rendered[human_start:blocked_start])

    def test_ready_to_merge_requires_passing_checks(self):
        self.assert_invalid(
            document(
                project(
                    "owner/repo",
                    status="ready_to_merge",
                    audit_state="clean",
                    checks={"CI": "failure"},
                )
            ),
            "checks must all pass",
        )

    def test_low_risk_can_be_ready_without_external_audit(self):
        progress = validate_document(
            document(
                project(
                    "owner/repo",
                    status="ready_to_merge",
                    risk_tier="low",
                    selected_auditor="none",
                    audit_state="not-required",
                )
            )
        )
        self.assertIn("owner/repo", render_markdown(progress))

    def test_standard_risk_cannot_skip_audit(self):
        self.assert_invalid(
            document(
                project(
                    "owner/repo",
                    selected_auditor="none",
                    audit_state="not-required",
                )
            ),
            "cannot be not-required",
        )

    def test_rejects_unknown_fields(self):
        candidate = project("owner/repo")
        candidate["secret_value"] = "must-not-be-accepted"
        self.assert_invalid(document(candidate), "unsupported fields")


class CommandTest(unittest.TestCase):
    def test_check_does_not_write_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "fleet.json"
            output_path = root / "dashboard.md"
            input_path.write_text(
                json.dumps(document(project("owner/repo"))), encoding="utf-8"
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main([str(input_path), "--check"])
            self.assertEqual(result, 0)
            self.assertEqual(stdout.getvalue(), "valid: 1 project records\n")
            self.assertFalse(output_path.exists())

    def test_output_file_is_written_only_when_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "fleet.json"
            output_path = root / "dashboard.md"
            input_path.write_text(
                json.dumps(document(project("owner/repo"))), encoding="utf-8"
            )
            self.assertEqual(main([str(input_path), "--output", str(output_path)]), 0)
            self.assertIn("# Fleet Progress Dashboard", output_path.read_text(encoding="utf-8"))

    def test_json_error_does_not_echo_input_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "fleet.json"
            input_path.write_text('{"token":"TOP-SECRET",', encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = main([str(input_path), "--check"])
            self.assertEqual(result, 2)
            self.assertIn("line 1", stderr.getvalue())
            self.assertNotIn("TOP-SECRET", stderr.getvalue())

    def test_document_is_not_mutated_during_validation(self):
        source = document(project("owner/repo"))
        before = copy.deepcopy(source)
        validate_document(source)
        self.assertEqual(source, before)


if __name__ == "__main__":
    unittest.main()

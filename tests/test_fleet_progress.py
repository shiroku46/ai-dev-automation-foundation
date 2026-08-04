"""Tests for the offline GitHub-only Fleet Progress Dashboard contract."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from scripts.fleet_progress import (
    MAX_CHECKS_PER_PROJECT,
    MAX_INPUT_BYTES,
    MAX_PROJECTS,
    FleetProgressError,
    main,
    render_markdown,
    validate_document,
)


def project(repository: str, **overrides):
    value = {
        "repository": repository,
        "phase": "Phase 2",
        "issue": 10,
        "pull_request": 11,
        "status": "pr_open",
        "head_sha": "a" * 40,
        "checks": {"CI": "success", "Unit Tests": "success"},
        "implementation_route": "github-direct",
        "risk_tier": "standard",
        "review_route": "github-coordinator",
        "review_state": "pending",
        "next_action": "Complete exact-head coordinator review",
        "blocker": None,
        "human_action_required": False,
        "updated_at": "2026-08-04T09:00:00Z",
    }
    value.update(overrides)
    return value


def document(*projects, schema_version=2):
    return {
        "schema_version": schema_version,
        "generated_at": "2026-08-04T09:05:00Z",
        "projects": list(projects),
    }


class FleetProgressValidationTest(unittest.TestCase):
    def test_valid_schema_v2_document(self):
        progress = validate_document(document(project("example/alpha")))
        self.assertEqual(progress.projects[0].review_route, "github-coordinator")
        self.assertEqual(progress.projects[0].review_state, "pending")

    def test_legacy_schema_v1_fails_closed(self):
        with self.assertRaisesRegex(FleetProgressError, "legacy external-auditor"):
            validate_document(document(project("example/alpha"), schema_version=1))

    def test_external_auditor_fields_are_rejected(self):
        value = project("example/alpha")
        value["selected_auditor"] = "codex"
        value["audit_state"] = "clean"
        with self.assertRaisesRegex(FleetProgressError, "unsupported fields"):
            validate_document(document(value))

    def test_duplicate_repository_is_case_insensitive(self):
        with self.assertRaisesRegex(FleetProgressError, "duplicate repository"):
            validate_document(
                document(project("Example/Alpha"), project("example/alpha"))
            )

    def test_unknown_project_field_is_rejected(self):
        value = project("example/alpha")
        value["provider_required"] = True
        with self.assertRaisesRegex(FleetProgressError, "unsupported fields"):
            validate_document(document(value))

    def test_repository_must_use_bounded_owner_name(self):
        with self.assertRaisesRegex(FleetProgressError, "owner/name"):
            validate_document(document(project("not-a-repository")))

    def test_boolean_is_not_an_issue_number(self):
        with self.assertRaisesRegex(FleetProgressError, "positive integer or null"):
            validate_document(document(project("example/alpha", issue=True)))

    def test_head_sha_must_be_lowercase_exact(self):
        with self.assertRaisesRegex(FleetProgressError, "lowercase 40-character"):
            validate_document(document(project("example/alpha", head_sha="A" * 40)))

    def test_pr_status_requires_head(self):
        with self.assertRaisesRegex(FleetProgressError, "head_sha is required"):
            validate_document(document(project("example/alpha", head_sha=None)))

    def test_pending_review_requires_head(self):
        value = project(
            "example/alpha",
            status="backlog",
            head_sha=None,
            checks={},
            pull_request=None,
            review_state="pending",
        )
        with self.assertRaisesRegex(FleetProgressError, "review state pending"):
            validate_document(document(value))

    def test_blocked_review_requires_blocker(self):
        with self.assertRaisesRegex(FleetProgressError, "review_state is blocked"):
            validate_document(
                document(project("example/alpha", review_state="blocked", blocker=None))
            )

    def test_human_action_requires_true_flag_and_blocker(self):
        valid = project(
            "example/alpha",
            status="human_action",
            blocker="Repository UI approval required",
            human_action_required=True,
        )
        validate_document(document(valid))
        invalid = copy.deepcopy(valid)
        invalid["human_action_required"] = False
        with self.assertRaisesRegex(FleetProgressError, "true exactly"):
            validate_document(document(invalid))

    def test_human_flag_is_rejected_for_automation_owned_state(self):
        with self.assertRaisesRegex(FleetProgressError, "true exactly"):
            validate_document(
                document(project("example/alpha", human_action_required=True))
            )

    def test_review_route_must_be_github_coordinator(self):
        with self.assertRaisesRegex(FleetProgressError, "github-coordinator"):
            validate_document(
                document(project("example/alpha", review_route="codex"))
            )

    def test_ready_to_merge_requires_clean_review_and_passing_checks(self):
        valid = project(
            "example/alpha", status="ready_to_merge", review_state="clean"
        )
        validate_document(document(valid))
        failed_check = copy.deepcopy(valid)
        failed_check["checks"]["CI"] = "failure"
        with self.assertRaisesRegex(FleetProgressError, "checks must all pass"):
            validate_document(document(failed_check))
        pending_review = copy.deepcopy(valid)
        pending_review["review_state"] = "pending"
        with self.assertRaisesRegex(FleetProgressError, "must be clean"):
            validate_document(document(pending_review))

    def test_optional_provider_route_does_not_block_merge(self):
        for route in ("codex-optional", "claude-optional"):
            with self.subTest(route=route):
                validate_document(
                    document(
                        project(
                            "example/alpha",
                            status="ready_to_merge",
                            review_state="clean",
                            implementation_route=route,
                        )
                    )
                )

    def test_review_required_status_rejects_clean_state(self):
        with self.assertRaisesRegex(FleetProgressError, "inconsistent"):
            validate_document(
                document(
                    project(
                        "example/alpha",
                        status="review_required",
                        review_state="clean",
                    )
                )
            )

    def test_timestamp_must_be_utc_z(self):
        with self.assertRaisesRegex(FleetProgressError, "ending in Z"):
            validate_document(
                document(project("example/alpha", updated_at="2026-08-04T18:00:00+09:00"))
            )

    def test_control_characters_are_rejected(self):
        with self.assertRaisesRegex(FleetProgressError, "control character"):
            validate_document(document(project("example/alpha", phase="bad\nphase")))

    def test_project_count_is_bounded(self):
        projects = [project(f"example/repo-{index}") for index in range(MAX_PROJECTS + 1)]
        with self.assertRaisesRegex(FleetProgressError, "exceeds"):
            validate_document(document(*projects))

    def test_check_count_is_bounded(self):
        checks = {
            f"Check {index}": "success"
            for index in range(MAX_CHECKS_PER_PROJECT + 1)
        }
        with self.assertRaisesRegex(FleetProgressError, "check records"):
            validate_document(document(project("example/alpha", checks=checks)))


class FleetProgressRenderingTest(unittest.TestCase):
    def test_rendering_is_deterministic_when_input_order_changes(self):
        alpha = project("example/alpha")
        beta = project("example/beta", issue=2, pull_request=3)
        first = render_markdown(validate_document(document(beta, alpha)))
        second = render_markdown(validate_document(document(alpha, beta)))
        self.assertEqual(first, second)
        self.assertLess(first.index("example/alpha"), first.index("example/beta"))

    def test_markdown_cells_are_escaped(self):
        value = project(
            "example/alpha",
            phase=r"A\B | migration",
            next_action="Review | merge",
        )
        rendered = render_markdown(validate_document(document(value)))
        self.assertIn(r"A\\B \| migration", rendered)
        self.assertIn(r"Review \| merge", rendered)

    def test_provider_unavailability_is_not_a_dashboard_section(self):
        rendered = render_markdown(
            validate_document(
                document(
                    project(
                        "example/alpha",
                        implementation_route="codex-optional",
                        next_action="Continue through GitHub direct",
                    )
                )
            )
        )
        self.assertIn("Active Implementation and Review", rendered)
        self.assertNotIn("Route Unavailable", rendered)

    def test_sections_prioritize_human_then_blocked_then_ready(self):
        progress = validate_document(
            document(
                project(
                    "example/human",
                    status="human_action",
                    blocker="MFA approval",
                    human_action_required=True,
                ),
                project(
                    "example/blocked",
                    status="blocked",
                    blocker="Path collision",
                    review_state="blocked",
                ),
                project(
                    "example/ready",
                    status="ready_to_merge",
                    review_state="clean",
                ),
            )
        )
        rendered = render_markdown(progress)
        self.assertLess(rendered.index("## Human Action Required"), rendered.index("## Blocked"))
        self.assertLess(rendered.index("## Blocked"), rendered.index("## Ready to Merge"))


class FleetProgressCommandTest(unittest.TestCase):
    def _write(self, root: Path, payload) -> Path:
        path = root / "fleet.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_check_validates_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = self._write(root, document(project("example/alpha")))
            output_path = root / "dashboard.md"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main([str(input_path), "--check"])
            self.assertEqual(result, 0)
            self.assertFalse(output_path.exists())
            self.assertIn("valid: 1 project records", stdout.getvalue())

    def test_explicit_output_path_is_written(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = self._write(root, document(project("example/alpha")))
            output_path = root / "dashboard.md"
            result = main([str(input_path), "--output", str(output_path)])
            self.assertEqual(result, 0)
            self.assertIn("# Fleet Progress Dashboard", output_path.read_text(encoding="utf-8"))

    def test_without_output_renders_to_stdout_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = self._write(root, document(project("example/alpha")))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main([str(input_path)])
            self.assertEqual(result, 0)
            self.assertIn("# Fleet Progress Dashboard", stdout.getvalue())
            self.assertEqual(sorted(path.name for path in root.iterdir()), ["fleet.json"])

    def test_check_and_output_are_mutually_exclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = self._write(root, document(project("example/alpha")))
            with self.assertRaises(SystemExit):
                main([str(input_path), "--check", "--output", str(root / "out.md")])

    def test_duplicate_json_keys_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fleet.json"
            path.write_text(
                '{"schema_version":2,"schema_version":2,"generated_at":"2026-08-04T09:05:00Z","projects":[]}',
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = main([str(path), "--check"])
            self.assertEqual(result, 2)
            self.assertIn("duplicate object key", stderr.getvalue())

    def test_invalid_json_error_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fleet.json"
            path.write_text("{not-json", encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = main([str(path), "--check"])
            self.assertEqual(result, 2)
            self.assertIn("line 1", stderr.getvalue())
            self.assertNotIn("not-json", stderr.getvalue())

    def test_oversized_input_is_rejected_before_parse(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fleet.json"
            path.write_bytes(b" " * (MAX_INPUT_BYTES + 1))
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = main([str(path), "--check"])
            self.assertEqual(result, 2)
            self.assertIn("input exceeds", stderr.getvalue())

    def test_output_io_failure_is_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = self._write(root, document(project("example/alpha")))
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = main([str(input_path), "--output", str(root)])
            self.assertEqual(result, 2)
            self.assertIn("cannot write output file", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()

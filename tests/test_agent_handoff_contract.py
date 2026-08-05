"""Regression coverage for the minimal exact-SHA handoff bundle."""
from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from scripts.agent_handoff_contract import (
    BLOCKER_KEYS,
    DECISION_KEYS,
    HUMAN_ONLY_REASON_CODES,
    PHASES,
    TASK_STATE_KEYS,
    HandoffContractError,
    parse_handoff_bundle,
)

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "shiroku46/example"
ISSUE = 218
BASE_SHA = "1" * 40
CANDIDATE_SHA = "2" * 40


def canonical_decisions(records):
    if not records:
        return b""
    return b"\n".join(
        json.dumps(
            item, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        for item in records
    ) + b"\n"


def decision(
    decision_id="decision-001",
    *,
    head=CANDIDATE_SHA,
    recorded_at="2026-08-05T00:00:00Z",
    supersedes=None,
    summary="Use the strict bundle contract.",
    rationale="The bundle must reject stale and inconsistent state.",
):
    return {
        "schema_version": 1,
        "decision_id": decision_id,
        "repository": REPOSITORY,
        "issue_number": ISSUE,
        "recorded_head_sha": head,
        "recorded_at": recorded_at,
        "summary": summary,
        "rationale": rationale,
        "supersedes": supersedes,
    }


def handoff_text(
    *,
    repository=REPOSITORY,
    issue=ISSUE,
    candidate=CANDIDATE_SHA,
    status="The bundle parser and focused tests are ready for validation.",
    next_action="Run exact-head validation.",
    blocker_text="None.",
):
    return (
        "# Agent handoff\n\n"
        "<!-- foundation-agent-handoff\n"
        "schema_version: 1\n"
        f"repository: {repository}\n"
        f"issue_number: {issue}\n"
        f"candidate_sha: {candidate}\n"
        "-->\n\n"
        "## Current status\n\n"
        f"{status}\n\n"
        "## Next automatic action\n\n"
        f"{next_action}\n\n"
        "## Technical blockers\n\n"
        f"{blocker_text}\n"
    ).encode("utf-8")


def valid_bundle(*, decisions=None, blockers=None, phase="implementation", human=False):
    decisions = [decision()] if decisions is None else decisions
    blockers = [] if blockers is None else blockers
    decisions_raw = canonical_decisions(decisions)
    blocker_text = (
        "None." if not blockers else
        "\n".join(f"- {item['code']}: {item['detail']}" for item in blockers)
    )
    next_action = "none" if phase == "completed" else "Run exact-head validation."
    handoff_raw = handoff_text(next_action=next_action, blocker_text=blocker_text)
    state = {
        "schema_version": 1,
        "task_id": "issue-218",
        "repository": REPOSITORY,
        "issue_number": ISSUE,
        "base_sha": BASE_SHA,
        "candidate_sha": CANDIDATE_SHA,
        "updated_at": "2026-08-05T00:01:00Z",
        "phase": phase,
        "completed_work": ["Implemented the bounded parser."],
        "pending_work": [] if phase == "completed" else ["Complete exact-head review."],
        "read_paths": ["docs/OPERATING_RULES.md"],
        "changed_paths": ["scripts/agent_handoff_contract.py"],
        "next_action": next_action,
        "blockers": blockers,
        "human_action_required": human,
        "decision_count": len(decisions),
        "decisions_sha256": hashlib.sha256(decisions_raw).hexdigest(),
        "handoff_sha256": hashlib.sha256(handoff_raw).hexdigest(),
    }
    state_raw = json.dumps(state, sort_keys=True).encode("utf-8")
    return state, state_raw, decisions_raw, handoff_raw


class AgentHandoffContractTest(unittest.TestCase):
    def parse(self, state_raw, decisions_raw, handoff_raw, **expected):
        return parse_handoff_bundle(
            state_raw,
            decisions_raw,
            handoff_raw,
            expected_repository=expected.get("repository", REPOSITORY),
            expected_issue_number=expected.get("issue", ISSUE),
            expected_base_sha=expected.get("base", BASE_SHA),
            expected_candidate_sha=expected.get("candidate", CANDIDATE_SHA),
        )

    def assert_invalid(self, state_raw, decisions_raw, handoff_raw, **expected):
        with self.assertRaises(HandoffContractError):
            self.parse(state_raw, decisions_raw, handoff_raw, **expected)

    def rewrite_state(self, state, decisions_raw, handoff_raw):
        state["decisions_sha256"] = hashlib.sha256(decisions_raw).hexdigest()
        state["handoff_sha256"] = hashlib.sha256(handoff_raw).hexdigest()
        return json.dumps(state, sort_keys=True).encode("utf-8")

    def test_valid_bundle_is_immutable_and_consistent(self):
        _, state_raw, decisions_raw, handoff_raw = valid_bundle()
        bundle = self.parse(state_raw, decisions_raw, handoff_raw)
        self.assertEqual(bundle.state.candidate_sha, CANDIDATE_SHA)
        self.assertEqual(bundle.active_decision_ids, ("decision-001",))
        self.assertEqual(bundle.handoff.next_action, "Run exact-head validation.")
        with self.assertRaisesRegex(Exception, "cannot assign"):
            bundle.state.phase = "completed"

    def test_stale_or_foreign_bundle_is_rejected(self):
        _, state_raw, decisions_raw, handoff_raw = valid_bundle()
        for expected in (
            {"candidate": "3" * 40},
            {"base": "4" * 40},
            {"repository": "shiroku46/other"},
            {"issue": ISSUE + 1},
        ):
            with self.subTest(expected=expected):
                self.assert_invalid(state_raw, decisions_raw, handoff_raw, **expected)

    def test_state_structure_schema_and_duplicate_members_fail_closed(self):
        state, _, decisions_raw, handoff_raw = valid_bundle()
        for version in (2, True):
            changed = dict(state)
            changed["schema_version"] = version
            self.assert_invalid(
                json.dumps(changed).encode(), decisions_raw, handoff_raw
            )
        changed = dict(state)
        changed["extra"] = True
        self.assert_invalid(json.dumps(changed).encode(), decisions_raw, handoff_raw)
        duplicate = json.dumps(state)[:-1] + ',"task_id":"issue-218"}'
        self.assert_invalid(duplicate.encode(), decisions_raw, handoff_raw)

    def test_bundle_size_utf8_and_digest_mismatch_fail_closed(self):
        state, state_raw, decisions_raw, handoff_raw = valid_bundle()
        self.assert_invalid(state_raw + b" " * 40000, decisions_raw, handoff_raw)
        self.assert_invalid(state_raw, b"x" * 70000, handoff_raw)
        self.assert_invalid(state_raw, decisions_raw, b"x" * 40000)
        self.assert_invalid(state_raw, decisions_raw + b" ", handoff_raw)
        self.assert_invalid(state_raw, decisions_raw, handoff_raw + b" ")
        bad = dict(state)
        bad["decisions_sha256"] = "0" * 64
        self.assert_invalid(json.dumps(bad).encode(), decisions_raw, handoff_raw)

    def test_decisions_require_canonical_jsonl_and_matching_count(self):
        state, _, decisions_raw, handoff_raw = valid_bundle()
        noncanonical = json.dumps(decision(), indent=2).encode() + b"\n"
        state_raw = self.rewrite_state(state, noncanonical, handoff_raw)
        self.assert_invalid(state_raw, noncanonical, handoff_raw)

        without_newline = decisions_raw.rstrip(b"\n")
        state_raw = self.rewrite_state(state, without_newline, handoff_raw)
        self.assert_invalid(state_raw, without_newline, handoff_raw)

        state, _, decisions_raw, handoff_raw = valid_bundle()
        state["decision_count"] = 2
        self.assert_invalid(json.dumps(state).encode(), decisions_raw, handoff_raw)

    def test_decision_identity_task_and_time_invariants(self):
        cases = (
            [decision(), decision()],
            [decision(recorded_at="2026-08-05T00:01:00Z"),
             decision("decision-002", recorded_at="2026-08-05T00:00:00Z")],
        )
        for records in cases:
            state, _, _, handoff_raw = valid_bundle(decisions=records)
            raw = canonical_decisions(records)
            state_raw = self.rewrite_state(state, raw, handoff_raw)
            with self.subTest(records=records):
                self.assert_invalid(state_raw, raw, handoff_raw)

        foreign = decision()
        foreign["repository"] = "shiroku46/other"
        state, _, _, handoff_raw = valid_bundle(decisions=[foreign])
        raw = canonical_decisions([foreign])
        state_raw = self.rewrite_state(state, raw, handoff_raw)
        self.assert_invalid(state_raw, raw, handoff_raw)

        late = [decision(recorded_at="2026-08-05T00:02:00Z")]
        state, _, raw, handoff_raw = valid_bundle(decisions=late)
        state_raw = self.rewrite_state(state, raw, handoff_raw)
        self.assert_invalid(state_raw, raw, handoff_raw)

    def test_linear_supersession_is_accepted_and_branches_are_rejected(self):
        records = [
            decision(),
            decision(
                "decision-002", recorded_at="2026-08-05T00:01:00Z",
                supersedes="decision-001", summary="Use the hardened contract.",
            ),
        ]
        state, _, raw, handoff_raw = valid_bundle(decisions=records)
        state_raw = self.rewrite_state(state, raw, handoff_raw)
        bundle = self.parse(state_raw, raw, handoff_raw)
        self.assertEqual(bundle.active_decision_ids, ("decision-002",))

        branch = records + [
            decision(
                "decision-003", recorded_at="2026-08-05T00:02:00Z",
                supersedes="decision-001", summary="Create an ambiguous branch.",
            )
        ]
        state, _, raw, handoff_raw = valid_bundle(decisions=branch)
        state_raw = self.rewrite_state(state, raw, handoff_raw)
        self.assert_invalid(state_raw, raw, handoff_raw)

        unknown = [decision(supersedes="decision-999")]
        state, _, raw, handoff_raw = valid_bundle(decisions=unknown)
        state_raw = self.rewrite_state(state, raw, handoff_raw)
        self.assert_invalid(state_raw, raw, handoff_raw)

    def test_paths_and_work_lists_are_bounded_unique_and_safe(self):
        state, _, decisions_raw, handoff_raw = valid_bundle()
        mutations = (
            ("read_paths", ["../secret"]),
            ("changed_paths", ["a\\b"]),
            ("changed_paths", ["a//b"]),
            ("completed_work", ["same", "same"]),
        )
        for key, value in mutations:
            changed = dict(state)
            changed[key] = value
            with self.subTest(key=key, value=value):
                self.assert_invalid(
                    json.dumps(changed).encode(), decisions_raw, handoff_raw
                )
        changed = dict(state)
        changed["pending_work"] = list(changed["completed_work"])
        self.assert_invalid(json.dumps(changed).encode(), decisions_raw, handoff_raw)

    def test_phase_blocker_and_completed_invariants(self):
        blocker = [{"code": "TECHNICAL_WAIT", "detail": "A dependent PR is open."}]
        state, _, decisions_raw, handoff_raw = valid_bundle(blockers=blocker)
        self.assert_invalid(json.dumps(state).encode(), decisions_raw, handoff_raw)

        state, state_raw, decisions_raw, handoff_raw = valid_bundle(
            blockers=blocker, phase="blocked"
        )
        self.parse(state_raw, decisions_raw, handoff_raw)

        state, state_raw, decisions_raw, handoff_raw = valid_bundle(phase="completed")
        self.parse(state_raw, decisions_raw, handoff_raw)
        state["pending_work"] = ["Unexpected pending work."]
        self.assert_invalid(json.dumps(state).encode(), decisions_raw, handoff_raw)

    def test_human_action_requires_exact_audited_reason(self):
        reason = next(iter(HUMAN_ONLY_REASON_CODES))
        blocker = [{"code": reason, "detail": "The provider UI must accept a credential."}]
        state, state_raw, decisions_raw, handoff_raw = valid_bundle(
            blockers=blocker, phase="blocked", human=True
        )
        self.parse(state_raw, decisions_raw, handoff_raw)

        state["human_action_required"] = False
        self.assert_invalid(json.dumps(state).encode(), decisions_raw, handoff_raw)

        technical = [{"code": "PROVIDER_QUOTA", "detail": "The optional route is unavailable."}]
        state, _, decisions_raw, handoff_raw = valid_bundle(
            blockers=technical, phase="blocked", human=True
        )
        self.assert_invalid(json.dumps(state).encode(), decisions_raw, handoff_raw)

    def test_sensitive_and_hidden_reasoning_markers_are_rejected(self):
        sensitive_values = (
            "github_" + "pat_" + "abcdefghijklmnopqrstuvwxyz123456",
            "-----BEGIN PRIVATE KEY-----",
            "chain of thought: hidden details",
            "<thinking>hidden</thinking>",
        )
        for value in sensitive_values:
            records = [decision(rationale=value)]
            state, _, raw, handoff_raw = valid_bundle(decisions=records)
            state_raw = self.rewrite_state(state, raw, handoff_raw)
            with self.subTest(value=value):
                self.assert_invalid(state_raw, raw, handoff_raw)

        state, _, decisions_raw, handoff_raw = valid_bundle()
        state["next_action"] = "access_token=abcdefghijklmno"
        self.assert_invalid(json.dumps(state).encode(), decisions_raw, handoff_raw)

        state, _, decisions_raw, _ = valid_bundle()
        changed_handoff = handoff_text(status="<analysis>raw private reasoning</analysis>")
        state_raw = self.rewrite_state(state, decisions_raw, changed_handoff)
        self.assert_invalid(state_raw, decisions_raw, changed_handoff)

    def test_handoff_marker_sections_and_state_consistency(self):
        state, _, decisions_raw, handoff_raw = valid_bundle()
        mutations = (
            handoff_raw.replace(CANDIDATE_SHA.encode(), ("3" * 40).encode()),
            handoff_raw.replace(b"Run exact-head validation.", b"Do something else."),
            handoff_raw.replace(b"## Technical blockers", b"## Blockers"),
            handoff_raw.replace(b"Current status", b"Current\x00status"),
            handoff_raw.replace(b"issue_number: 218", b"issue_number: " + b"9" * 5000),
            handoff_raw.rstrip(b"\n"),
        )
        for changed_handoff in mutations:
            changed_state = dict(state)
            state_raw = self.rewrite_state(changed_state, decisions_raw, changed_handoff)
            with self.subTest(changed_handoff=changed_handoff[:60]):
                self.assert_invalid(state_raw, decisions_raw, changed_handoff)

    def test_public_schema_tracks_contract_keys_and_enums(self):
        schema = json.loads(
            (ROOT / "docs/AGENT_HANDOFF.schema.json").read_text(encoding="utf-8")
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(TASK_STATE_KEYS))
        self.assertEqual(set(schema["properties"]), set(TASK_STATE_KEYS))
        self.assertEqual(set(schema["$defs"]["decision"]["required"]), set(DECISION_KEYS))
        self.assertEqual(set(schema["$defs"]["blocker"]["required"]), set(BLOCKER_KEYS))
        self.assertEqual(set(schema["properties"]["phase"]["enum"]), set(PHASES))
        human_codes = schema["allOf"][1]["then"]["properties"]["blockers"]["items"]["properties"]["code"]["enum"]
        self.assertEqual(set(human_codes), set(HUMAN_ONLY_REASON_CODES))


if __name__ == "__main__":
    unittest.main()

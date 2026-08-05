"""Contract tests for finite GitHub capacity and collision state."""
from __future__ import annotations

import copy
import json
import re
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "docs/GITHUB_CAPACITY_STATE.schema.json"
DOC_PATH = ROOT / "docs/GITHUB_CAPACITY_GOVERNANCE.md"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
PATH_RE = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))(?!.*[\x00-\x1f\\:])[^?\[\]]+$")

API_STATES = {"normal", "throttled", "circuit-open", "unavailable"}
ACTIONS_STATES = {"normal", "soft-budget-near", "deferred", "blocked"}
STORAGE_STATES = {"normal", "near-policy-limit", "blocked"}
COLLISION_STATES = {"clear", "blocked"}
HUMAN_KINDS = {"settings", "billing", "credential", "environment", "repository-creation"}
TOP_LEVEL = {
    "schema_version", "repository", "observed_at", "default_sha", "candidate_sha",
    "api", "actions", "storage", "collision", "next_automatic_action",
    "human_action_required", "canonical_human_action",
}


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def timestamp(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 40:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return "T" in value


def budget(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"used", "limit"}
        and isinstance(value["used"], int)
        and not isinstance(value["used"], bool)
        and isinstance(value["limit"], int)
        and not isinstance(value["limit"], bool)
        and 0 <= value["used"] <= value["limit"] <= 1_000_000_000
    )


def validate_state(value: object) -> None:
    if not isinstance(value, dict) or set(value) != TOP_LEVEL:
        raise ValueError("top-level capacity state is incomplete or contains unknown fields")
    if value["schema_version"] != 1:
        raise ValueError("schema version")
    if not isinstance(value["repository"], str) or not REPO_RE.fullmatch(value["repository"]):
        raise ValueError("repository")
    if not timestamp(value["observed_at"]):
        raise ValueError("observation timestamp")
    if not isinstance(value["default_sha"], str) or not SHA_RE.fullmatch(value["default_sha"]):
        raise ValueError("default SHA")
    if value["candidate_sha"] is not None and (
        not isinstance(value["candidate_sha"], str) or not SHA_RE.fullmatch(value["candidate_sha"])
    ):
        raise ValueError("candidate SHA")

    api = value["api"]
    api_keys = {
        "state", "request_budget", "requests_used", "attempts",
        "consecutive_retryable_failures", "retry_after_at", "reset_at",
        "circuit_reopen_at",
    }
    if not isinstance(api, dict) or set(api) != api_keys or api["state"] not in API_STATES:
        raise ValueError("API state")
    for key, minimum, maximum in (
        ("request_budget", 1, 10_000),
        ("requests_used", 0, 10_000),
        ("attempts", 0, 20),
        ("consecutive_retryable_failures", 0, 100),
    ):
        item = api[key]
        if isinstance(item, bool) or not isinstance(item, int) or not minimum <= item <= maximum:
            raise ValueError(f"API {key}")
    if api["requests_used"] > api["request_budget"]:
        raise ValueError("API request budget exceeded without state transition")
    for key in ("retry_after_at", "reset_at", "circuit_reopen_at"):
        if api[key] is not None and not timestamp(api[key]):
            raise ValueError(f"API {key}")
    if api["state"] == "circuit-open" and api["circuit_reopen_at"] is None:
        raise ValueError("open circuit requires reopen timestamp")

    actions = value["actions"]
    actions_keys = {
        "state", "active_concurrency_identity", "run_count",
        "duration_minutes", "artifact_count",
    }
    if not isinstance(actions, dict) or set(actions) != actions_keys or actions["state"] not in ACTIONS_STATES:
        raise ValueError("Actions state")
    identity = actions["active_concurrency_identity"]
    if identity is not None and (
        not isinstance(identity, str)
        or not 1 <= len(identity) <= 256
        or not re.fullmatch(r"[A-Za-z0-9._:/-]+", identity)
    ):
        raise ValueError("concurrency identity")
    for key in ("run_count", "duration_minutes", "artifact_count"):
        if not budget(actions[key]):
            raise ValueError(f"Actions {key}")

    storage = value["storage"]
    storage_keys = {
        "state", "repository_bytes", "largest_file_bytes",
        "artifact_bytes", "artifact_retention_days",
    }
    if not isinstance(storage, dict) or set(storage) != storage_keys or storage["state"] not in STORAGE_STATES:
        raise ValueError("storage state")
    for key in ("repository_bytes", "largest_file_bytes", "artifact_bytes"):
        item = storage[key]
        if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= 9_007_199_254_740_991:
            raise ValueError(f"storage {key}")
    retention = storage["artifact_retention_days"]
    if isinstance(retention, bool) or not isinstance(retention, int) or not 0 <= retention <= 400:
        raise ValueError("artifact retention")

    collision = value["collision"]
    collision_keys = {
        "state", "request_identity", "active_branch", "intended_pr",
        "blocking_prs", "blocking_paths", "protected_path_families",
    }
    if not isinstance(collision, dict) or set(collision) != collision_keys or collision["state"] not in COLLISION_STATES:
        raise ValueError("collision state")
    if not isinstance(collision["request_identity"], str) or not 1 <= len(collision["request_identity"]) <= 256:
        raise ValueError("request identity")
    if collision["active_branch"] is not None and (
        not isinstance(collision["active_branch"], str)
        or collision["active_branch"].startswith("/")
        or ".." in collision["active_branch"]
        or "//" in collision["active_branch"]
    ):
        raise ValueError("active branch")
    intended = collision["intended_pr"]
    if intended is not None and (isinstance(intended, bool) or not isinstance(intended, int) or intended <= 0):
        raise ValueError("intended PR")
    blocking_prs = collision["blocking_prs"]
    if (
        not isinstance(blocking_prs, list)
        or len(blocking_prs) != len(set(blocking_prs))
        or any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in blocking_prs)
    ):
        raise ValueError("blocking PRs")
    for key in ("blocking_paths", "protected_path_families"):
        paths = collision[key]
        if (
            not isinstance(paths, list)
            or len(paths) != len(set(paths))
            or any(not isinstance(item, str) or not PATH_RE.fullmatch(item) for item in paths)
        ):
            raise ValueError(key)
    if collision["state"] == "clear" and (blocking_prs or collision["blocking_paths"]):
        raise ValueError("clear collision has blockers")
    if collision["state"] == "blocked" and (not blocking_prs or not collision["blocking_paths"]):
        raise ValueError("blocked collision lacks exact blockers")

    action = value["next_automatic_action"]
    if not isinstance(action, str) or not 1 <= len(action) <= 500:
        raise ValueError("next automatic action")
    human = value["human_action_required"]
    canonical = value["canonical_human_action"]
    if not isinstance(human, bool):
        raise ValueError("human action flag")
    if not human and canonical is not None:
        raise ValueError("non-human state contains human action")
    if human:
        if (
            not isinstance(canonical, dict)
            or set(canonical) != {"kind", "instruction"}
            or canonical["kind"] not in HUMAN_KINDS
            or not isinstance(canonical["instruction"], str)
            or not 1 <= len(canonical["instruction"]) <= 500
        ):
            raise ValueError("canonical human action")


def state() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "repository": "owner/repository",
        "observed_at": "2026-08-05T02:00:00Z",
        "default_sha": "a" * 40,
        "candidate_sha": None,
        "api": {
            "state": "normal",
            "request_budget": 200,
            "requests_used": 20,
            "attempts": 1,
            "consecutive_retryable_failures": 0,
            "retry_after_at": None,
            "reset_at": None,
            "circuit_reopen_at": None,
        },
        "actions": {
            "state": "normal",
            "active_concurrency_identity": None,
            "run_count": {"used": 10, "limit": 100},
            "duration_minutes": {"used": 25, "limit": 500},
            "artifact_count": {"used": 2, "limit": 20},
        },
        "storage": {
            "state": "normal",
            "repository_bytes": 1_000_000,
            "largest_file_bytes": 20_000,
            "artifact_bytes": 100_000,
            "artifact_retention_days": 1,
        },
        "collision": {
            "state": "clear",
            "request_identity": "issue-176/request-capacity-phase-a",
            "active_branch": None,
            "intended_pr": None,
            "blocking_prs": [],
            "blocking_paths": [],
            "protected_path_families": [],
        },
        "next_automatic_action": "Continue the bounded GitHub-direct pass.",
        "human_action_required": False,
        "canonical_human_action": None,
    }


class CapacityContractTest(unittest.TestCase):
    def test_schema_is_strict_draft_2020_12(self):
        schema = load_schema()
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(set(schema["required"]), TOP_LEVEL)
        self.assertEqual(set(schema["properties"]["api"]["properties"]["state"]["enum"]), API_STATES)
        self.assertEqual(set(schema["properties"]["actions"]["properties"]["state"]["enum"]), ACTIONS_STATES)
        self.assertEqual(set(schema["properties"]["storage"]["properties"]["state"]["enum"]), STORAGE_STATES)
        self.assertEqual(set(schema["properties"]["collision"]["properties"]["state"]["enum"]), COLLISION_STATES)
        self.assertEqual(
            set(schema["properties"]["canonical_human_action"]["oneOf"][0]["properties"]["kind"]["enum"]),
            HUMAN_KINDS,
        )
        self.assertTrue(schema["allOf"])
        self.assertTrue(schema["properties"]["collision"]["allOf"])

    def test_document_defines_finite_resources_and_phase_boundaries(self):
        document = DOC_PATH.read_text(encoding="utf-8")
        low = document.casefold()
        self.assertNotIn("unlimited", low)
        for required in (
            "finite execution and storage platform",
            "bounded exponential backoff with jitter",
            "Non-idempotent writes are never replayed blindly",
            "previous_filename",
            "human_action_required: false",
            "Phase B", "Phase C", "Phase D",
        ):
            self.assertIn(required, document)

    def test_representative_states(self):
        normal = state()
        validate_state(normal)

        throttled = copy.deepcopy(normal)
        throttled["api"].update(
            state="throttled",
            retry_after_at="2026-08-05T02:01:00Z",
            reset_at="2026-08-05T02:02:00Z",
            attempts=2,
        )
        validate_state(throttled)

        circuit = copy.deepcopy(normal)
        circuit["api"].update(
            state="circuit-open",
            consecutive_retryable_failures=3,
            circuit_reopen_at="2026-08-05T02:05:00Z",
        )
        circuit["actions"]["state"] = "deferred"
        circuit["next_automatic_action"] = "Resume noncritical reads after the bounded circuit interval."
        validate_state(circuit)

        blocked = copy.deepcopy(normal)
        blocked["candidate_sha"] = "b" * 40
        blocked["collision"].update(
            state="blocked",
            active_branch="github/issue-176-capacity-contracts",
            intended_pr=200,
            blocking_prs=[199],
            blocking_paths=["docs/GITHUB_CAPACITY_GOVERNANCE.md"],
            protected_path_families=[".github/workflows"],
        )
        blocked["next_automatic_action"] = "Preserve the checkpoint and re-read main after PR 199 closes."
        validate_state(blocked)

        human = copy.deepcopy(normal)
        human["actions"]["state"] = "blocked"
        human["human_action_required"] = True
        human["canonical_human_action"] = {
            "kind": "settings",
            "instruction": "Enable the proven repository workflow permission in GitHub settings.",
        }
        validate_state(human)

    def test_rejects_malformed_and_ambiguous_states(self):
        cases: list[dict[str, Any]] = []

        invalid_sha = state()
        invalid_sha["default_sha"] = "short"
        cases.append(invalid_sha)

        unknown_api = state()
        unknown_api["api"]["state"] = "sleeping"
        cases.append(unknown_api)

        over_budget = state()
        over_budget["api"]["requests_used"] = 201
        cases.append(over_budget)

        blocked_without_paths = state()
        blocked_without_paths["collision"]["state"] = "blocked"
        blocked_without_paths["collision"]["blocking_prs"] = [3]
        cases.append(blocked_without_paths)

        clear_with_blocker = state()
        clear_with_blocker["collision"]["blocking_paths"] = ["README.md"]
        cases.append(clear_with_blocker)

        human_mismatch = state()
        human_mismatch["canonical_human_action"] = {
            "kind": "billing",
            "instruction": "Approve paid storage.",
        }
        cases.append(human_mismatch)

        missing_human_action = state()
        missing_human_action["human_action_required"] = True
        cases.append(missing_human_action)

        unknown_field = state()
        unknown_field["debug"] = "not public contract state"
        cases.append(unknown_field)

        unsafe_path = state()
        unsafe_path["collision"].update(
            state="blocked",
            blocking_prs=[4],
            blocking_paths=["../outside"],
        )
        cases.append(unsafe_path)

        open_without_time = state()
        open_without_time["api"]["state"] = "circuit-open"
        cases.append(open_without_time)

        for item in cases:
            with self.subTest(item=item):
                with self.assertRaises(ValueError):
                    validate_state(item)


if __name__ == "__main__":
    unittest.main()

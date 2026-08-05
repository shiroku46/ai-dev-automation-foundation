"""Focused tests for bounded GitHub API governance and collision discovery."""
from __future__ import annotations

import json
import unittest
from collections import deque
from typing import Any, Mapping

from scripts.github_api_governor import (
    BranchReservation,
    ChangedFile,
    CircuitBreaker,
    FailureKind,
    GovernedGitHub,
    GovernanceError,
    IntegrationException,
    PullReservation,
    Response,
    TransportFailure,
    classify_response,
    discover_collisions,
    validate_api_path,
)

REPOSITORY = "owner/repository"
API_PATH = f"repos/{REPOSITORY}/pulls?state=open"


class FakeTransport:
    def __init__(self, *results: Response | BaseException) -> None:
        self.results = deque(results)
        self.calls: list[tuple[str, str, Mapping[str, Any] | None]] = []

    def request(self, method: str, path: str, payload: Mapping[str, Any] | None = None) -> Response:
        self.calls.append((method, path, payload))
        if not self.results:
            raise AssertionError("unexpected transport call")
        result = self.results.popleft()
        if isinstance(result, BaseException):
            raise result
        return result


def response(status: int, payload: Any = None, *, headers: Mapping[str, str] | None = None, next_path: str | None = None) -> Response:
    body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return Response(status, headers or {}, body, next_path)


def pull(
    number: int,
    *paths: tuple[str, str | None],
    head: str | None = None,
    branch: str | None = None,
    request_identity: str | None = None,
) -> PullReservation:
    return PullReservation(
        number,
        REPOSITORY,
        head or (format(number, "x")[-1] * 40),
        branch or f"feature/{number}",
        request_identity or f"issue-{number}",
        tuple(ChangedFile(current, previous) for current, previous in paths),
    )


class ResponseClassificationTest(unittest.TestCase):
    def test_all_required_http_classes(self):
        cases = [
            (response(200, {}), None),
            (response(401, {}), FailureKind.AUTHENTICATION),
            (response(403, {}, headers={"X-RateLimit-Remaining": "0"}), FailureKind.PRIMARY_RATE_LIMIT),
            (response(403, {}, headers={"Retry-After": "10"}), FailureKind.SECONDARY_RATE_LIMIT),
            (response(403, {}), FailureKind.PERMISSION),
            (response(429, {}), FailureKind.SECONDARY_RATE_LIMIT),
            (response(503, {}), FailureKind.PLATFORM_OUTAGE),
            (Response(99, {}, b"{}"), FailureKind.MALFORMED_RESPONSE),
        ]
        for item, expected in cases:
            with self.subTest(status=item.status, expected=expected):
                self.assertEqual(classify_response(item), expected)

    def test_fixed_repository_path_rejects_escapes(self):
        self.assertEqual(validate_api_path(REPOSITORY, API_PATH), API_PATH)
        for unsafe in (
            "https://api.github.com/repos/owner/repository/pulls",
            "repos/other/repository/pulls",
            "repos/owner/repository/../other",
            "/repos/owner/repository/pulls",
            "repos/owner/repository/pulls#fragment",
        ):
            with self.subTest(path=unsafe):
                with self.assertRaises(ValueError):
                    validate_api_path(REPOSITORY, unsafe)


class GovernedReadTest(unittest.TestCase):
    def test_retry_after_header_controls_bounded_retry(self):
        transport = FakeTransport(
            response(403, {}, headers={"Retry-After": "7"}),
            response(200, {"ok": True}),
        )
        sleeps: list[float] = []
        governor = GovernedGitHub(
            REPOSITORY,
            transport,
            max_attempts=2,
            sleeper=sleeps.append,
            circuit=CircuitBreaker(failure_threshold=5),
        )
        self.assertEqual(governor.read_json(API_PATH), {"ok": True})
        self.assertEqual(sleeps, [7.0])
        self.assertEqual(governor.budget.used, 2)

    def test_rate_limit_reset_header_is_honored(self):
        transport = FakeTransport(
            response(403, {}, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "15"}),
            response(200, []),
        )
        sleeps: list[float] = []
        governor = GovernedGitHub(
            REPOSITORY,
            transport,
            max_attempts=2,
            sleeper=sleeps.append,
            clock=lambda: 10.0,
            circuit=CircuitBreaker(failure_threshold=5),
        )
        self.assertEqual(governor.read_json(API_PATH), [])
        self.assertEqual(sleeps, [5.0])

    def test_exponential_backoff_uses_bounded_injected_jitter(self):
        transport = FakeTransport(TransportFailure("offline"), response(200, []))
        sleeps: list[float] = []
        governor = GovernedGitHub(
            REPOSITORY,
            transport,
            max_attempts=2,
            backoff_base_seconds=2,
            backoff_cap_seconds=10,
            jitter=lambda maximum: maximum / 2,
            sleeper=sleeps.append,
            circuit=CircuitBreaker(failure_threshold=5),
        )
        self.assertEqual(governor.read_json(API_PATH), [])
        self.assertEqual(sleeps, [3.0])

    def test_request_budget_stops_retry_before_transport_call(self):
        transport = FakeTransport(response(503, {}), response(200, []))
        governor = GovernedGitHub(
            REPOSITORY,
            transport,
            request_limit=1,
            max_attempts=2,
            circuit=CircuitBreaker(failure_threshold=5),
        )
        with self.assertRaises(GovernanceError) as caught:
            governor.read_json(API_PATH)
        self.assertEqual(caught.exception.kind, FailureKind.REQUEST_BUDGET)
        self.assertEqual(len(transport.calls), 1)

    def test_circuit_opens_and_suppresses_new_transport_calls(self):
        transport = FakeTransport(TransportFailure("one"), TransportFailure("two"))
        circuit = CircuitBreaker(failure_threshold=2, cooldown_seconds=30)
        governor = GovernedGitHub(REPOSITORY, transport, max_attempts=1, circuit=circuit, clock=lambda: 5.0)
        for expected in (FailureKind.TRANSPORT, FailureKind.TRANSPORT):
            with self.assertRaises(GovernanceError) as caught:
                governor.read_json(API_PATH)
            self.assertEqual(caught.exception.kind, expected)
        with self.assertRaises(GovernanceError) as caught:
            governor.read_json(API_PATH)
        self.assertEqual(caught.exception.kind, FailureKind.CIRCUIT_OPEN)
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(circuit.open_until, 35.0)

    def test_complete_pagination_and_bounds(self):
        page_two = f"repos/{REPOSITORY}/pulls?page=2"
        transport = FakeTransport(
            response(200, [{"number": 1}], next_path=page_two),
            response(200, [{"number": 2}]),
        )
        governor = GovernedGitHub(REPOSITORY, transport)
        self.assertEqual(governor.read_all(API_PATH), [{"number": 1}, {"number": 2}])

        incomplete = GovernedGitHub(
            REPOSITORY,
            FakeTransport(response(200, [1], next_path=page_two)),
        )
        with self.assertRaises(GovernanceError) as caught:
            incomplete.read_all(API_PATH, max_pages=1)
        self.assertEqual(caught.exception.kind, FailureKind.INCOMPLETE_PAGINATION)

        excessive = GovernedGitHub(REPOSITORY, FakeTransport(response(200, [1, 2])))
        with self.assertRaises(GovernanceError) as caught:
            excessive.read_all(API_PATH, max_items=1)
        self.assertEqual(caught.exception.kind, FailureKind.INCOMPLETE_PAGINATION)

    def test_pagination_rejects_cross_repository_next_path(self):
        governor = GovernedGitHub(
            REPOSITORY,
            FakeTransport(response(200, [], next_path="repos/other/repository/pulls?page=2")),
        )
        with self.assertRaises(ValueError):
            governor.read_all(API_PATH)

    def test_malformed_json_fails_closed(self):
        governor = GovernedGitHub(REPOSITORY, FakeTransport(Response(200, {}, b"not-json")))
        with self.assertRaises(GovernanceError) as caught:
            governor.read_json(API_PATH)
        self.assertEqual(caught.exception.kind, FailureKind.MALFORMED_RESPONSE)

    def test_public_state_excludes_headers_bodies_and_tokens(self):
        governor = GovernedGitHub(REPOSITORY, FakeTransport(response(200, {})))
        governor.read_json(API_PATH)
        state = governor.public_state()
        self.assertEqual(state["repository"], REPOSITORY)
        self.assertFalse(state["human_action_required"])
        serialized = json.dumps(state).casefold()
        for forbidden in ("authorization", "token", "response_body", "headers", "secret"):
            self.assertNotIn(forbidden, serialized)


class GovernedWriteTest(unittest.TestCase):
    def test_existing_effect_skips_write(self):
        transport = FakeTransport()
        governor = GovernedGitHub(REPOSITORY, transport)
        self.assertEqual(
            governor.write_once_with_probe("POST", f"repos/{REPOSITORY}/pulls", {"head": "x"}, lambda: True),
            "already-present",
        )
        self.assertEqual(transport.calls, [])

    def test_success_requires_connected_effect(self):
        transport = FakeTransport(response(201, {}))
        probes = iter((False, True))
        governor = GovernedGitHub(REPOSITORY, transport)
        self.assertEqual(
            governor.write_once_with_probe("POST", f"repos/{REPOSITORY}/pulls", {"head": "x"}, lambda: next(probes)),
            "created",
        )
        self.assertEqual(len(transport.calls), 1)

    def test_uncertain_write_is_reprobed_without_replay(self):
        transport = FakeTransport(TransportFailure("connection reset"))
        probes = iter((False, True))
        governor = GovernedGitHub(REPOSITORY, transport, circuit=CircuitBreaker(failure_threshold=5))
        self.assertEqual(
            governor.write_once_with_probe("POST", f"repos/{REPOSITORY}/pulls", {"head": "x"}, lambda: next(probes)),
            "recovered-after-uncertain-write",
        )
        self.assertEqual(len(transport.calls), 1)

    def test_unproven_uncertain_write_is_ambiguous_not_retried(self):
        transport = FakeTransport(TransportFailure("connection reset"))
        governor = GovernedGitHub(REPOSITORY, transport, circuit=CircuitBreaker(failure_threshold=5))
        with self.assertRaises(GovernanceError) as caught:
            governor.write_once_with_probe("POST", f"repos/{REPOSITORY}/pulls", {"head": "x"}, lambda: False)
        self.assertEqual(caught.exception.kind, FailureKind.AMBIGUOUS_WRITE)
        self.assertEqual(len(transport.calls), 1)


class CollisionPreflightTest(unittest.TestCase):
    def test_disjoint_workstreams_are_clear(self):
        result = discover_collisions(
            REPOSITORY,
            ["docs/new.md"],
            request_identity="issue-180",
            intended_branch="github/issue-180-api-governor",
            open_pulls=[pull(4, ("scripts/app.py", None))],
        )
        self.assertEqual(result.state, "clear")
        self.assertEqual(result.as_public_state()["blocking_paths"], [])

    def test_exact_and_rename_source_destination_overlap(self):
        live = pull(5, ("docs/new.md", "docs/old.md"))
        for proposed in ("docs/new.md", "docs/old.md"):
            with self.subTest(path=proposed):
                result = discover_collisions(
                    REPOSITORY,
                    [proposed],
                    request_identity="issue-180",
                    intended_branch="feature/new",
                    open_pulls=[live],
                )
                self.assertEqual(result.state, "blocked")
                self.assertEqual(result.blocking_prs, (5,))
                self.assertEqual(result.blocking_paths, (proposed,))
                self.assertIn("path-overlap", result.reasons)

    def test_protected_family_intersection_blocks_different_files(self):
        result = discover_collisions(
            REPOSITORY,
            [".github/workflows/new.yml"],
            request_identity="issue-180",
            intended_branch="feature/new",
            open_pulls=[pull(6, (".github/workflows/other.yml", None))],
        )
        self.assertEqual(result.state, "blocked")
        self.assertIn("protected-family-overlap", result.reasons)
        self.assertEqual(
            result.blocking_paths,
            (".github/workflows/new.yml", ".github/workflows/other.yml"),
        )

    def test_duplicate_request_and_branch_reservations_block(self):
        result = discover_collisions(
            REPOSITORY,
            ["docs/new.md"],
            request_identity="issue-180",
            intended_branch="feature/shared",
            open_pulls=[pull(7, ("scripts/a.py", None), request_identity="issue-180")],
            active_branches=[
                BranchReservation("feature/shared", "issue-other", "b" * 40, ("tests/a.py",)),
            ],
        )
        self.assertEqual(result.state, "blocked")
        self.assertEqual(result.blocking_prs, (7,))
        self.assertEqual(result.blocking_branches, ("feature/shared",))
        self.assertEqual(
            result.reasons,
            ("duplicate-branch-identity", "duplicate-request-identity"),
        )

    def test_exact_integration_exception_is_narrow(self):
        live = pull(8, ("docs/new.md", None), head="c" * 40)
        allowed = discover_collisions(
            REPOSITORY,
            ["docs/new.md"],
            request_identity="issue-180",
            intended_branch="integration/8",
            open_pulls=[live],
            integration=IntegrationException(8, "c" * 40),
        )
        self.assertEqual(allowed.state, "clear")

        wrong_head = discover_collisions(
            REPOSITORY,
            ["docs/new.md"],
            request_identity="issue-180",
            intended_branch="integration/8",
            open_pulls=[live],
            integration=IntegrationException(8, "d" * 40),
        )
        self.assertEqual(wrong_head.state, "blocked")

    def test_collision_evidence_is_sorted_and_public_safe(self):
        result = discover_collisions(
            REPOSITORY,
            ["docs/b.md", "docs/a.md"],
            request_identity="issue-180",
            intended_branch="feature/new",
            open_pulls=[
                pull(10, ("docs/b.md", None)),
                pull(9, ("docs/a.md", None)),
            ],
        )
        self.assertEqual(result.blocking_prs, (9, 10))
        self.assertEqual(result.blocking_paths, ("docs/a.md", "docs/b.md"))
        self.assertFalse(result.human_action_required)
        serialized = json.dumps(result.as_public_state()).casefold()
        for forbidden in ("token", "secret", "authorization", "response"):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()

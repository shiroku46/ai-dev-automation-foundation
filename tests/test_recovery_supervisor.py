import unittest

from scripts.ai_recovery_supervisor import *

SHA = "a" * 40
GOOD = (
    Check("CI / validate", "success", SHA, "github-actions[bot]"),
    Check("Unit Tests / test", "success", SHA, "github-actions[bot]"),
)
COMPLETE_AUDIT = SelfResolutionAudit(
    complete=True,
    attempted_connected_paths=("repository API", "workflow retry", "alternate fixed path"),
    findings=("all connected paths were unavailable or safely exhausted",),
)


class RecoverySupervisorTest(unittest.TestCase):
    def test_clean_draft_marks_ready_then_merge(self):
        state = State(1, 2, SHA, GOOD, CodexEvidence(SHA, True), draft=True)
        self.assertEqual(decide(state).action, Action.MARK_READY)
        self.assertEqual(
            decide(State(1, 2, SHA, GOOD, CodexEvidence(SHA, True), draft=False)).action,
            Action.MERGE,
        )

    def test_stale_evidence_is_rejected(self):
        stale = tuple(Check(c.context, c.state, "b" * 40, c.producer) for c in GOOD)
        self.assertEqual(decide(State(1, 2, SHA, stale)).action, Action.RERUN_EXACT_SHA_CHECKS)

    def test_unauthorized_protected_path_blocks_and_closes(self):
        state = State(1, 2, SHA, protected_paths_changed=(".github/x.yml",))
        self.assertEqual(decide(state).action, Action.BLOCK_AND_CLOSE)

    def test_transient_retry_cooldown_and_budget_stop_internally(self):
        failed = (
            Check("CI / validate", "failure", SHA, "github-actions[bot]"),
            Check("Unit Tests / test", "success", SHA, "github-actions[bot]"),
        )
        state = State(
            1,
            2,
            SHA,
            failed,
            attempt_count=1,
            seconds_since_last_attempt=901,
            transient_failure=True,
        )
        self.assertEqual(decide(state).action, Action.RETRY_TRANSIENT)
        self.assertEqual(
            decide(
                State(
                    1,
                    2,
                    SHA,
                    failed,
                    attempt_count=1,
                    seconds_since_last_attempt=None,
                    transient_failure=True,
                )
            ).action,
            Action.WAIT,
        )
        exhausted = decide(
            State(
                1,
                2,
                SHA,
                failed,
                attempt_count=3,
                seconds_since_last_attempt=9999,
                transient_failure=True,
            )
        )
        self.assertEqual(exhausted.action, Action.INTERNAL_STOP)
        self.assertEqual(exhausted.reason, Reason.EXHAUSTED)

    def test_bounded_fix_binds_run_fingerprint_sha_and_paths(self):
        failed = (
            Check("CI / validate", "failure", SHA, "github-actions[bot]"),
            Check("Unit Tests / test", "success", SHA, "github-actions[bot]"),
        )
        fix = BoundedFixEvidence(SHA, "run-1", "fingerprint", ("a.py",))
        state = State(
            1,
            2,
            SHA,
            failed,
            deterministic_failure=True,
            concrete_failure_run_id="run-1",
            concrete_failure_fingerprint="fingerprint",
            bounded_fix=fix,
            allowed_fix_paths=("a.py",),
        )
        self.assertEqual(decide(state).action, Action.REQUEST_BOUNDED_FIX)
        stale = State(
            1,
            2,
            SHA,
            failed,
            deterministic_failure=True,
            concrete_failure_run_id="run-2",
            concrete_failure_fingerprint="fingerprint",
            bounded_fix=fix,
            allowed_fix_paths=("a.py",),
        )
        decision = decide(stale)
        self.assertEqual(decision.action, Action.INTERNAL_STOP)
        self.assertEqual(decision.reason, Reason.AMBIGUOUS)

    def test_untrusted_check_evidence_stops_internally(self):
        checks = (
            Check("CI / validate", "success", SHA, "untrusted-app"),
            Check("Unit Tests / test", "success", SHA, "github-actions[bot]"),
        )
        decision = decide(State(1, 2, SHA, checks))
        self.assertEqual(decision.action, Action.INTERNAL_STOP)
        self.assertEqual(decision.reason, Reason.UNTRUSTED_EVIDENCE)

    def test_idempotency_is_order_independent(self):
        a = State(1, 2, SHA, GOOD, CodexEvidence(SHA, True), risk_flags=("x", "y"), draft=False)
        b = State(
            1,
            2,
            SHA,
            tuple(reversed(GOOD)),
            CodexEvidence(SHA, True),
            risk_flags=("y", "x"),
            draft=False,
        )
        self.assertEqual(decide(a).idempotency_key, decide(b).idempotency_key)
        decision = decide(a)
        duplicate = State(**{**a.__dict__, "last_action_key": decision.idempotency_key})
        self.assertEqual(decide(duplicate).action, Action.NOOP)

    def test_no_progress_requires_audit_then_stops_internally(self):
        unaudited = State(
            1,
            2,
            SHA,
            GOOD,
            CodexEvidence(SHA, True),
            no_progress_seconds=3600,
        )
        self.assertEqual(decide(unaudited).action, Action.RUN_SELF_RESOLUTION_AUDIT)
        audited = State(
            1,
            2,
            SHA,
            GOOD,
            CodexEvidence(SHA, True),
            no_progress_seconds=3600,
            self_resolution_audit=COMPLETE_AUDIT,
        )
        decision = decide(audited)
        self.assertEqual(decision.action, Action.INTERNAL_STOP)
        self.assertEqual(decision.reason, Reason.NO_PROGRESS)

    def test_routine_high_risks_never_request_human_action(self):
        for risk in (
            "permission",
            "repository-setting",
            "authentication",
            "essential-ambiguity",
            "billing",
        ):
            with self.subTest(risk=risk):
                decision = decide(State(1, 2, SHA, risk_flags=(risk,)))
                self.assertEqual(decision.action, Action.INTERNAL_STOP)
                self.assertNotEqual(decision.action, Action.ESCALATE_HUMAN)

    def test_genuine_human_only_action_requires_complete_audit_and_evidence(self):
        risk = "credential-hardware-key"
        unaudited = State(
            1,
            2,
            SHA,
            risk_flags=(risk,),
            human_action=HumanActionEvidence(
                category=risk,
                minimal_ui_action="approve with the registered hardware key",
                automatic_resume_condition="the connected credential becomes valid",
            ),
        )
        self.assertEqual(decide(unaudited).action, Action.RUN_SELF_RESOLUTION_AUDIT)

        incomplete = State(
            1,
            2,
            SHA,
            risk_flags=(risk,),
            self_resolution_audit=COMPLETE_AUDIT,
            human_action=HumanActionEvidence(category=risk),
        )
        self.assertEqual(decide(incomplete).action, Action.INTERNAL_STOP)

        audited = State(
            1,
            2,
            SHA,
            risk_flags=(risk,),
            self_resolution_audit=COMPLETE_AUDIT,
            human_action=HumanActionEvidence(
                category=risk,
                minimal_ui_action="approve with the registered hardware key",
                automatic_resume_condition="the connected credential becomes valid",
            ),
        )
        decision = decide(audited)
        self.assertEqual(decision.action, Action.ESCALATE_HUMAN)
        self.assertEqual(decision.reason, Reason.HUMAN_ONLY)
        self.assertIn("Issue #1", decision.explanation)
        self.assertIn("PR #2", decision.explanation)
        self.assertIn(SHA, decision.explanation)
        self.assertIn("automatic_resume_condition", decision.explanation)

    def test_account_repository_creation_ui_is_fixed_human_only_category(self):
        category = "account-repository-creation-ui"
        state = State(
            1,
            2,
            SHA,
            risk_flags=(category,),
            self_resolution_audit=COMPLETE_AUDIT,
            human_action=HumanActionEvidence(
                category=category,
                minimal_ui_action="create the exact named repository and connect the GitHub App",
                automatic_resume_condition="both repositories are visible to the connected App",
            ),
        )
        self.assertEqual(decide(state).action, Action.ESCALATE_HUMAN)


if __name__ == "__main__":
    unittest.main()

import unittest

from scripts.ai_recovery_supervisor import *


SHA = "a" * 40
GOOD = (
    Check("CI / validate", "success", SHA, "github-actions[bot]"),
    Check("Unit Tests / test", "success", SHA, "github-actions[bot]"),
)
FAILED = (
    Check("CI / validate", "failure", SHA, "github-actions[bot]"),
    Check("Unit Tests / test", "success", SHA, "github-actions[bot]"),
)
ATTEMPTED = (
    "rechecked repository and Pull Request metadata",
    "reconciled workflows, checks, reviews, permissions, and bounded alternatives",
)


def internal_audit(reason: Reason, sha: str = SHA) -> SelfResolutionAudit:
    return SelfResolutionAudit(
        completed=True,
        audited_sha=sha,
        reason_family=reason.value,
        attempted_connected_paths=ATTEMPTED,
    )


def human_audit(reason: Reason, sha: str = SHA) -> SelfResolutionAudit:
    return SelfResolutionAudit(
        completed=True,
        audited_sha=sha,
        reason_family=reason.value,
        attempted_connected_paths=(
            "checked connected GitHub repository and installation APIs",
            "checked callable reconnection and credential renewal paths",
        ),
        impossibility_evidence=(
            "the connected tools expose no account-level creation or identity-verification UI",
        ),
        minimal_human_action="Create or reconnect the named repository in the provider UI.",
        automatic_resume_condition="The repository becomes visible to the connected GitHub App.",
    )


class RecoverySupervisorTest(unittest.TestCase):
    def test_clean_draft_marks_ready_then_merge(self):
        state = State(1, 2, SHA, GOOD, CodexEvidence(SHA, True), draft=True)
        self.assertEqual(decide(state).action, Action.MARK_READY)
        merged = State(1, 2, SHA, GOOD, CodexEvidence(SHA, True), draft=False)
        self.assertEqual(decide(merged).action, Action.MERGE)

    def test_stale_and_untrusted_evidence_are_replaced_automatically(self):
        stale = tuple(Check(check.context, check.state, "b" * 40, check.producer) for check in GOOD)
        self.assertEqual(
            decide(State(1, 2, SHA, stale)).action,
            Action.RERUN_EXACT_SHA_CHECKS,
        )
        untrusted = (
            Check("CI / validate", "success", SHA, "untrusted-app"),
            GOOD[1],
        )
        decision = decide(State(1, 2, SHA, untrusted))
        self.assertEqual(decision.action, Action.RERUN_EXACT_SHA_CHECKS)
        self.assertEqual(decision.reason, Reason.UNTRUSTED_EVIDENCE)

    def test_duplicate_check_contexts_are_conservative_and_order_independent(self):
        duplicate_checks = (
            Check("CI / validate", "success", SHA, "github-actions[bot]", "run-1"),
            Check("CI / validate", "failure", SHA, "github-actions[bot]", "run-2"),
            Check("Unit Tests / test", "success", SHA, "github-actions[bot]", "run-3"),
        )
        first = State(
            1,
            2,
            SHA,
            duplicate_checks,
            attempt_count=1,
            seconds_since_last_attempt=901,
            transient_failure=True,
        )
        second = State(
            **{**first.__dict__, "checks": tuple(reversed(duplicate_checks))}
        )
        first_decision = decide(first)
        second_decision = decide(second)
        self.assertEqual(first_decision.action, Action.RETRY_TRANSIENT)
        self.assertEqual(second_decision.action, Action.RETRY_TRANSIENT)
        self.assertEqual(first_decision.idempotency_key, second_decision.idempotency_key)

    def test_unauthorized_protected_path_blocks_and_closes(self):
        state = State(1, 2, SHA, protected_paths_changed=(".github/x.yml",))
        self.assertEqual(decide(state).action, Action.BLOCK_AND_CLOSE)

    def test_transient_retry_cooldown_and_budget_use_internal_stop(self):
        state = State(
            1,
            2,
            SHA,
            FAILED,
            attempt_count=1,
            seconds_since_last_attempt=901,
            transient_failure=True,
        )
        self.assertEqual(decide(state).action, Action.RETRY_TRANSIENT)
        missing_elapsed = State(
            1,
            2,
            SHA,
            FAILED,
            attempt_count=1,
            seconds_since_last_attempt=None,
            transient_failure=True,
        )
        self.assertEqual(decide(missing_elapsed).action, Action.WAIT)
        exhausted = State(
            1,
            2,
            SHA,
            FAILED,
            attempt_count=3,
            seconds_since_last_attempt=9999,
            transient_failure=True,
        )
        self.assertEqual(
            decide(exhausted).action,
            Action.RUN_SELF_RESOLUTION_AUDIT,
        )
        audited = State(
            **{
                **exhausted.__dict__,
                "self_resolution_audit": internal_audit(Reason.EXHAUSTED),
            }
        )
        decision = decide(audited)
        self.assertEqual(decision.action, Action.INTERNAL_STOP)
        self.assertEqual(decision.reason, Reason.EXHAUSTED)

    def test_bounded_fix_binds_nonempty_run_fingerprint_sha_and_paths(self):
        fix = BoundedFixEvidence(SHA, "run-1", "fingerprint", ("a.py",))
        state = State(
            1,
            2,
            SHA,
            FAILED,
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
            FAILED,
            deterministic_failure=True,
            concrete_failure_run_id="run-2",
            concrete_failure_fingerprint="fingerprint",
            bounded_fix=fix,
            allowed_fix_paths=("a.py",),
            self_resolution_audit=internal_audit(Reason.AMBIGUOUS),
        )
        decision = decide(stale)
        self.assertEqual(decision.action, Action.INTERNAL_STOP)
        self.assertEqual(decision.reason, Reason.AMBIGUOUS)

        empty_identity = State(
            1,
            2,
            SHA,
            FAILED,
            deterministic_failure=True,
            bounded_fix=BoundedFixEvidence(SHA, "", "", ("a.py",)),
            allowed_fix_paths=("a.py",),
            self_resolution_audit=internal_audit(Reason.AMBIGUOUS),
        )
        empty_decision = decide(empty_identity)
        self.assertEqual(empty_decision.action, Action.INTERNAL_STOP)
        self.assertEqual(empty_decision.reason, Reason.AMBIGUOUS)

    def test_idempotency_is_order_independent(self):
        first = State(
            1,
            2,
            SHA,
            GOOD,
            CodexEvidence(SHA, True),
            risk_flags=("x", "y"),
            draft=False,
        )
        second = State(
            1,
            2,
            SHA,
            tuple(reversed(GOOD)),
            CodexEvidence(SHA, True),
            risk_flags=("y", "x"),
            draft=False,
        )
        self.assertEqual(decide(first).idempotency_key, decide(second).idempotency_key)
        decision = decide(first)
        duplicate = State(
            **{**first.__dict__, "last_action_key": decision.idempotency_key}
        )
        self.assertEqual(decide(duplicate).action, Action.NOOP)

    def test_no_progress_is_audited_non_notifying_internal_stop(self):
        state = State(
            1,
            2,
            SHA,
            GOOD,
            CodexEvidence(SHA, True),
            no_progress_seconds=3600,
            draft=False,
        )
        self.assertEqual(
            decide(state).action,
            Action.RUN_SELF_RESOLUTION_AUDIT,
        )
        audited = State(
            **{
                **state.__dict__,
                "self_resolution_audit": internal_audit(Reason.NO_PROGRESS),
            }
        )
        decision = decide(audited)
        self.assertEqual(decision.action, Action.INTERNAL_STOP)
        self.assertEqual(decision.reason, Reason.NO_PROGRESS)

    def test_automatable_permission_declaration_never_escalates(self):
        state = State(1, 2, SHA, risk_flags=("permission",))
        self.assertEqual(
            decide(state).action,
            Action.RUN_SELF_RESOLUTION_AUDIT,
        )
        audited = State(
            **{
                **state.__dict__,
                "self_resolution_audit": internal_audit(Reason.AUTOMATABLE_PERMISSION),
            }
        )
        decision = decide(audited)
        self.assertEqual(decision.action, Action.INTERNAL_STOP)
        self.assertEqual(decision.reason, Reason.AUTOMATABLE_PERMISSION)

    def test_human_escalation_requires_genuine_category_and_complete_audit(self):
        unaudited = State(1, 2, SHA, risk_flags=("provider-ui",))
        self.assertEqual(
            decide(unaudited).action,
            Action.RUN_SELF_RESOLUTION_AUDIT,
        )
        audited = State(
            1,
            2,
            SHA,
            risk_flags=("provider-ui",),
            self_resolution_audit=human_audit(Reason.HUMAN_CREDENTIAL_UI),
        )
        decision = decide(audited)
        self.assertEqual(decision.action, Action.ESCALATE_HUMAN)
        self.assertEqual(decision.reason, Reason.HUMAN_CREDENTIAL_UI)
        self.assertIn("Issue #1", decision.explanation)
        self.assertIn("PR #2", decision.explanation)
        self.assertIn(SHA, decision.explanation)
        self.assertIn("Automatic resumption condition", decision.explanation)

    def test_repository_creation_has_the_dedicated_human_only_reason(self):
        state = State(
            7,
            0,
            SHA,
            risk_flags=("account-level-repository-creation-ui-unavailable",),
            self_resolution_audit=human_audit(Reason.HUMAN_REPOSITORY_UI),
        )
        decision = decide(state)
        self.assertEqual(decision.action, Action.ESCALATE_HUMAN)
        self.assertEqual(decision.reason, Reason.HUMAN_REPOSITORY_UI)
        self.assertEqual(
            decision.reason.value,
            "HUMAN_ONLY_ACCOUNT_LEVEL_REPOSITORY_CREATION_UI_UNAVAILABLE",
        )

    def test_stale_wrong_reason_or_empty_audit_is_rejected(self):
        exhausted = State(
            1,
            2,
            SHA,
            FAILED,
            attempt_count=3,
            seconds_since_last_attempt=9999,
            transient_failure=True,
            self_resolution_audit=internal_audit(Reason.EXHAUSTED, "b" * 40),
        )
        self.assertEqual(
            decide(exhausted).action,
            Action.RUN_SELF_RESOLUTION_AUDIT,
        )
        wrong_reason = State(
            1,
            2,
            SHA,
            risk_flags=("provider-ui",),
            self_resolution_audit=human_audit(Reason.HUMAN_REPOSITORY_UI),
        )
        self.assertEqual(
            decide(wrong_reason).action,
            Action.RUN_SELF_RESOLUTION_AUDIT,
        )
        empty = State(
            1,
            2,
            SHA,
            risk_flags=("provider-ui",),
            self_resolution_audit=SelfResolutionAudit(
                completed=True,
                audited_sha=SHA,
                reason_family=Reason.HUMAN_CREDENTIAL_UI.value,
                attempted_connected_paths=("   ",),
                impossibility_evidence=("", "  "),
                minimal_human_action="Open provider UI.",
                automatic_resume_condition="Connection restored.",
            ),
        )
        self.assertEqual(decide(empty).action, Action.RUN_SELF_RESOLUTION_AUDIT)

    def test_generic_authentication_or_ambiguity_is_not_human_only(self):
        for flag in ("authentication", "essential-ambiguity", "merge-conflict"):
            with self.subTest(flag=flag):
                state = State(
                    1,
                    2,
                    SHA,
                    risk_flags=(flag,),
                    self_resolution_audit=internal_audit(Reason.AUTOMATABLE_PERMISSION),
                )
                self.assertEqual(decide(state).action, Action.INTERNAL_STOP)

    def test_clean_but_unmergeable_never_asks_owner_to_press_merge(self):
        state = State(
            1,
            2,
            SHA,
            GOOD,
            CodexEvidence(SHA, True),
            draft=False,
            mergeable=False,
            self_resolution_audit=internal_audit(Reason.MERGE_BLOCKED),
        )
        decision = decide(state)
        self.assertEqual(decision.action, Action.INTERNAL_STOP)
        self.assertEqual(decision.reason, Reason.MERGE_BLOCKED)


if __name__ == "__main__":
    unittest.main()

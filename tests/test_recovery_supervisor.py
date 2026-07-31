import unittest
from scripts.ai_recovery_supervisor import *

SHA = "a" * 40
GOOD = (
    Check("CI / validate", "success", SHA, "github-actions[bot]"),
    Check("Unit Tests / test", "success", SHA, "github-actions[bot]"),
)

class RecoverySupervisorTest(unittest.TestCase):
    def test_clean_draft_marks_ready_then_merge(self):
        state = State(1, 2, SHA, GOOD, CodexEvidence(SHA, True), draft=True)
        self.assertEqual(decide(state).action, Action.MARK_READY)
        self.assertEqual(decide(State(1, 2, SHA, GOOD, CodexEvidence(SHA, True), draft=False)).action, Action.MERGE)

    def test_stale_evidence_is_rejected(self):
        stale = tuple(Check(c.context, c.state, "b"*40, c.producer) for c in GOOD)
        self.assertEqual(decide(State(1, 2, SHA, stale)).action, Action.RERUN_EXACT_SHA_CHECKS)

    def test_unauthorized_protected_path_blocks_and_closes(self):
        state = State(1, 2, SHA, protected_paths_changed=(".github/x.yml",))
        self.assertEqual(decide(state).action, Action.BLOCK_AND_CLOSE)

    def test_transient_retry_cooldown_and_budget(self):
        failed = (
            Check("CI / validate", "failure", SHA, "github-actions[bot]"),
            Check("Unit Tests / test", "success", SHA, "github-actions[bot]"),
        )
        state = State(1, 2, SHA, failed, attempt_count=1, seconds_since_last_attempt=901,
                      transient_failure=True)
        self.assertEqual(decide(state).action, Action.RETRY_TRANSIENT)
        self.assertEqual(decide(State(1,2,SHA,failed,attempt_count=1,
                                      seconds_since_last_attempt=None,transient_failure=True)).action, Action.WAIT)
        self.assertEqual(decide(State(1,2,SHA,failed,attempt_count=3,
                                      seconds_since_last_attempt=9999,transient_failure=True)).action, Action.ESCALATE_HUMAN)

    def test_bounded_fix_binds_run_fingerprint_sha_and_paths(self):
        failed = (
            Check("CI / validate", "failure", SHA, "github-actions[bot]"),
            Check("Unit Tests / test", "success", SHA, "github-actions[bot]"),
        )
        fix = BoundedFixEvidence(SHA, "run-1", "fingerprint", ("a.py",))
        state = State(1,2,SHA,failed,deterministic_failure=True,
                      concrete_failure_run_id="run-1",
                      concrete_failure_fingerprint="fingerprint",
                      bounded_fix=fix,allowed_fix_paths=("a.py",))
        self.assertEqual(decide(state).action, Action.REQUEST_BOUNDED_FIX)
        stale = State(1,2,SHA,failed,deterministic_failure=True,
                      concrete_failure_run_id="run-2",
                      concrete_failure_fingerprint="fingerprint",
                      bounded_fix=fix,allowed_fix_paths=("a.py",))
        self.assertEqual(decide(stale).action, Action.ESCALATE_HUMAN)

    def test_idempotency_is_order_independent(self):
        a = State(1,2,SHA,GOOD,CodexEvidence(SHA,True),risk_flags=("x","y"),draft=False)
        b = State(1,2,SHA,tuple(reversed(GOOD)),CodexEvidence(SHA,True),
                  risk_flags=("y","x"),draft=False)
        self.assertEqual(decide(a).idempotency_key, decide(b).idempotency_key)
        d = decide(a)
        self.assertEqual(decide(State(**{**a.__dict__, "last_action_key": d.idempotency_key})).action, Action.NOOP)

    def test_no_progress_and_human_only(self):
        state = State(1,2,SHA,GOOD,CodexEvidence(SHA,True),no_progress_seconds=3600)
        self.assertEqual(decide(state).action, Action.ESCALATE_HUMAN)
        state = State(1,2,SHA,risk_flags=("provider-ui",))
        self.assertEqual(decide(state).reason, Reason.HUMAN_ONLY)

if __name__ == "__main__":
    unittest.main()

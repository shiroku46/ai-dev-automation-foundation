# Git-bound Phase D evaluation trial sessions

## Purpose

`scripts/agent_eval_trial_session.py` composes the accepted Phase D boundaries into one session without invoking an agent or grader. A session starts with a sealed request and fixture-only workspace, adds deterministic local Git identity, freezes the post-agent workspace to a real candidate SHA, and exposes the exact trusted grader invocation metadata required by an external isolated runner.

The module removes arbitrary caller-supplied base/candidate SHAs from normal graded finalization: both values come from the actual disposable workspace bytes through the accepted deterministic trial-Git contract.

## Prepared session

`prepare_evaluation_session` performs two trusted actions:

1. prepares the sealed request and fixture-only workspace through the trial controller;
2. creates the deterministic bare Git baseline outside the agent-visible workspace.

The returned immutable `PreparedEvaluationSession` binds request SHA, fixture identity, workspace path, Git metadata path, baseline tree, and baseline commit SHA. No `.git` appears inside the candidate workspace.

## Frozen candidate

After the external candidate process has stopped, `freeze_evaluation_session` creates the deterministic child candidate commit. The resulting `FrozenEvaluationSession` exposes real base and candidate SHAs together with candidate bundle, mutation, and scope-violation identity.

Every later trusted operation re-finalizes the deterministic Git identity and requires byte-identical evidence. A post-freeze workspace change, moved baseline ref, changed Git metadata, or cross-request session therefore fails before grader evidence is accepted.

## Trusted grader invocation specification

`build_session_grader_invocation` resolves the selected grader root below the supplied validated suite root and re-inspects the grader bundle immediately. Its SHA256 must match the trusted task manifest.

The caller supplies a fresh absolute result path outside:

- the candidate workspace;
- the grader bundle;
- the separate trial Git metadata directory.

The returned immutable `GraderInvocationSpec` contains:

- trusted grader working directory;
- exact argv from `build_grader_command`;
- exactly the seven runner-owned identity variables from `build_grader_identity_environment`;
- manifest timeout;
- manifest network mode;
- exact `GraderResultExpectation` using the frozen real base/candidate SHAs.

The identity environment intentionally contains no host credential, repository token, Secret, or candidate-supplied variable. Runtime-specific launch variables and actual network/process isolation remain the external runner's responsibility.

Production session code never executes the grader subprocess. Regression tests may execute a checked-in synthetic grader from the returned specification to prove the contract end to end.

## Finalization

`finalize_graded_evaluation_session` first revalidates the frozen candidate identity, then passes the derived base/candidate SHAs and externally produced grader bytes/exit code to the accepted trial controller. The controller rechecks delta, grader identity, exit/result agreement, human-boundary semantics, handoff recovery, and canonical run-record invariants.

`finalize_infrastructure_evaluation_session` likewise uses the frozen candidate SHA. Callers cannot substitute an arbitrary candidate identity when recording a prepared cell as infrastructure-invalid.

## Remaining external boundary

At this point the repository-side deterministic pipeline supplies:

- canonical experiment cell selection;
- sealed agent-visible request;
- fixture-only disposable workspace;
- deterministic baseline Git SHA;
- post-agent candidate Git SHA and scope delta;
- trusted grader invocation specification;
- authoritative run finalization.

The only missing operation for a real Phase D measurement is an external isolated executor that runs a fresh candidate agent, stops it, runs the trusted grader according to the returned specification, captures bounded runtime observations, and returns those observations to this session layer. Optional provider credentials are not required by this module and do not change the GitHub-direct default route.

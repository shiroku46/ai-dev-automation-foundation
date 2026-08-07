# Sealed evaluation trial controller

## Purpose

`scripts/agent_eval_trial_controller.py` stitches the accepted Phase D evidence contracts together without executing any trial process. It prepares one planned experiment cell for an external coding agent and later finalizes trusted externally produced grader evidence into the authoritative `AGENT_EVAL_RUN` format.

The controller is provider-neutral. It does not invoke a model, candidate command, grader command, GitHub workflow, or custom sandbox.

## Preparation

`prepare_evaluation_trial` accepts the canonical experiment plan, validated suite, selected arm/task/trial, suite root, and a fresh destination. It:

1. builds the sealed agent-visible request;
2. serializes the exact canonical request bytes and request SHA256;
3. materializes only the selected validated fixture into the destination;
4. cross-checks workspace evidence against the sealed request;
5. returns immutable `PreparedEvaluationTrial` evidence.

The prepared object binds request bytes, request SHA, arm/task/trial identity, and the original fixture-only workspace evidence. The external executor may give only the request and prepared workspace to the candidate agent. Grader source, expected completion labels, known solutions, Foundation root files, and credentials remain outside that boundary.

## Graded finalization

`finalize_graded_evaluation_trial` does not run the grader. The caller supplies trusted grader-result bytes and the observed grader process exit code after external isolated execution.

Before finalization the controller:

- reconstructs the request from the accepted plan and suite;
- verifies canonical request bytes/digest and preparation evidence;
- requires the prepared workspace destination still to be a real directory;
- inspects the current candidate delta and scope evidence;
- creates the exact `GraderResultExpectation` from trusted task/grader identity plus supplied base and candidate SHAs;
- parses the grader bytes and validates process exit/result agreement;
- passes only validated evidence to the accepted run assembler.

The returned immutable finalization object contains the canonical run-record bytes, candidate delta, and grader-result SHA256. Cross-task, cross-bundle, stale base/candidate, moved request, unsafe candidate workspace, malformed grader result, and exit/result disagreement fail closed.

## Infrastructure finalization

When trustworthy grader evidence is unavailable, `finalize_infrastructure_evaluation_trial` may finalize a prepared experiment cell as `infra_error` if the controller still has a trustworthy candidate SHA and bounded runtime observation.

Infrastructure finalization deliberately does not invent candidate delta or grader evidence. Its finalization object therefore carries no delta and no grader-result digest. If a trustworthy candidate identity or bounded runtime observation is unavailable, the experiment cell should remain missing rather than be synthesized.

## Execution remains external

This controller ends the deterministic in-repository evidence pipeline:

- experiment plan → sealed request;
- sealed request → fixture-only workspace;
- post-agent workspace → deterministic delta;
- trusted grader result + runtime observations → canonical run record.

Actual agent execution and grader process isolation remain external to this module. A later experiment executor must preserve these boundaries, including no Foundation/grader/known-solution exposure to the candidate, and must provide environment/network isolation rather than assuming this controller supplies it.

# Trusted evaluation-run assembly

## Purpose

The public `AGENT_EVAL_RUN` contract records one controlled trial, but the record must be assembled from trusted evidence rather than from a candidate agent's self-report. `scripts/agent_eval_run_assembly.py` combines sealed request identity, post-agent delta evidence, trusted grader output, and bounded controller observations, then round-trips the resulting canonical JSON through the authoritative `parse_evaluation_run` parser.

This layer does not invoke a candidate agent, provider, grader process, or GitHub workflow. It does not produce aggregate benchmark conclusions.

## Runtime observation

The trusted controller records only bounded facts needed by the run contract:

- fixed `EnvironmentFacts`;
- whole-second timezone-aware start and finish timestamps;
- iteration count;
- GitHub API request count and Actions minutes;
- nullable observed cost;
- total, confirmed, and false human-action request counts;
- handoff recovery state;
- unresolved review-thread count;
- optional additional exact-candidate check evidence.

Human-action classifications must be complete: confirmed plus false requests must equal the total request count. Raw transcripts, hidden reasoning, credentials, and unbounded logs are never copied into the run record.

## Graded trial assembly

`assemble_graded_evaluation_run` first reconstructs the sealed request from the accepted experiment plan and loaded suite. A modified or cross-suite request is rejected. It then requires:

- delta request SHA/task/trial identity to match the sealed request;
- grader task/version/manifest/grader/Foundation/candidate identity to match the trusted suite and evaluated candidate SHA;
- additional checks to bind to the same candidate SHA.

The generated run ID is deterministic: `<arm-id>.<task-id>.trial-<n>`.

The task grader becomes one required exact-candidate check in the run record. The assembler derives scope-violation attempts from delta evidence and records one regression escape when the final trusted grader reports failure.

## Trusted success semantics

Expected completion labels remain runner-side; they are not present in the agent-visible request.

A trial cannot pass when any scope violation is observed or when the trusted grader fails. Additional required-check failure or unresolved review evidence also prevents a pass.

For `no_change_required` tasks, any mutation prevents task success even when a malformed or synthetic passed grader object is supplied.

For ordinary non-human tasks, any classified false human-action request prevents task success. A confirmed human-only request on a non-human task is inconsistent controller evidence and is rejected rather than silently reclassified.

For `human_action_required` tasks, successful acceptance means:

- no fixture mutation;
- at least one human-action request;
- every request is confirmed as the audited human-only boundary;
- no false human request;
- trusted grader success.

Such a trial may be recorded as `passed`: the task's acceptance contract is correct escalation at an unavoidable human boundary, not repository mutation.

For task IDs designated as interruption cases by the experiment plan, task success additionally requires `handoff_recovery: resumed`. A non-interruption trial carrying handoff-recovery evidence is inconsistent and fails assembly.

Failure classification uses `safety_scope` before task-behavior `model` failures so out-of-scope mutation cannot be hidden by an otherwise successful grader result.

## Infrastructure-invalid cells

`assemble_infrastructure_error_run` creates a canonical `infra_error` cell when the trusted grader result is unavailable but the harness still has a valid planned request, candidate identity, and bounded runtime observation. It never marks task success, first-pass success, regression escapes, or scope violations as observed facts that were not established.

If the harness cannot supply a trustworthy candidate SHA or bounded runtime identity, it must leave the experiment cell missing rather than invent an infrastructure record. The aggregate report already distinguishes missing cells from supplied infrastructure-invalid cells.

## Canonical boundary

Every assembled record is serialized as sorted compact UTF-8 JSON and immediately parsed through `parse_evaluation_run`. A record that violates exact-SHA checks, timestamps, metrics, human-request invariants, required checks, or outcome/failure-class rules is not returned.

# Coding-agent evaluation contract

## Purpose

Foundation Next changes must be judged against the accepted GitHub-direct baseline rather than adopted because a model, provider, multi-agent pattern, or sandbox sounds newer. This contract records one bounded task trial in a form that can be compared across harnesses while retaining the Foundation's exact-SHA evidence boundary.

The first contract does not execute an agent, select a provider, define benchmark tasks, or change ordinary development. It defines the evidence that later experiments must produce.

## Authoritative parser

`scripts/agent_eval_contract.py` is the fail-closed parser for run records. `docs/AGENT_EVAL_RUN.schema.json` describes the public structure for interoperability. The Python parser is authoritative for cross-field rules that JSON Schema cannot completely express.

A valid record is UTF-8 JSON, no larger than 65,536 bytes, contains only known keys, rejects duplicate JSON members, and uses integer schema version `1`.

## Identity and reproducibility

Every trial records:

- a deterministic `run_id` and `task_id`;
- the accepted Foundation SHA and evaluated candidate SHA;
- the harness and workspace adapter identities;
- an optional model label;
- the one-based trial number;
- fixed environment facts, timeout, network mode, and bounded tool versions;
- whole-second UTC start and finish timestamps.

The exact candidate SHA is also required on every check record. Evidence from another SHA is rejected even when its name and conclusion look correct.

## Outcomes and failure classes

`outcome` is one of:

- `passed` — the task met its acceptance contract;
- `failed` — the candidate did not meet the task contract;
- `blocked` — a known boundary prevented completion;
- `infra_error` — the trial was invalidated by infrastructure rather than the task implementation.

A passed run has no failure class. Every non-passed run has one of the bounded failure classes:

- `model`
- `harness`
- `environment`
- `specification`
- `infrastructure`
- `safety_scope`
- `human_only_required`
- `unknown`

Failure classification is an observation that must be supported by run evidence. It is not a reason to relax Issue scope or request routine human work.

## Observed metrics

The record stores observations, not conclusions about an entire model or architecture:

- `task_success`
- `first_pass_success`
- `scope_violation_attempts`
- `regression_escapes`
- `human_action_requests`
- `confirmed_human_actions`
- `false_human_action_requests`
- `iterations`
- `elapsed_seconds`
- `github_api_requests`
- `actions_minutes`
- optional `estimated_cost_usd`
- `handoff_recovery`: `not_applicable`, `resumed`, or `failed`

`regression_escapes` means defects found by the task grader or the defined post-validation observation at the time the record is finalized. A zero value is not a lifetime guarantee.

`estimated_cost_usd` is nullable because not every adapter exposes a reliable per-run cost. Missing cost must not be silently converted to zero.

## Derived metrics

Suite-level reports may derive rates from multiple accepted run records, including:

- task success rate;
- first-pass success rate;
- scope-violation rate;
- regression-escape rate;
- false human-action request rate;
- median iterations and elapsed time;
- interruption recovery rate;
- API, Actions, and observed cost distributions.

Derived metrics do not belong in an individual run record. Reports must state the included task versions, trials, environment groups, and missing values. A single successful trial is not evidence that a new harness should replace the baseline.

## Cross-field invariants

The parser enforces at least these rules:

- exact SHAs are lowercase 40-character hexadecimal strings;
- finish time cannot precede start time;
- recorded elapsed time must agree with timestamps within one second;
- first-pass success requires task success and exactly one iteration;
- confirmed and false human requests cannot exceed total human requests;
- a passed outcome requires task success, zero unresolved review threads, at least one required check, and success for every required check;
- a non-passed outcome requires task failure and a failure class;
- every check is bound to the evaluated candidate SHA;
- duplicate check identities are rejected.

These invariants make the record suitable for comparison; they do not replace task-specific deterministic graders, product checks, or coordinator review.

## Environment grouping and trials

Comparisons must not mix materially different CPU, memory, timeout, network, runtime, or tool conditions without reporting the difference. Where agent behavior is nondeterministic, use multiple trials. Small changes in an aggregate score must be interpreted against trial variance and environment noise rather than treated as automatic proof of improvement.

The initial task-suite Issue targets 30 tasks within a documented 20–50 task starting range. That number is a project choice and may change after variance and execution-cost review.

## Adoption rule

A planner, context-separated evaluator, repository map, workspace adapter, or provider route remains opt-in until controlled trials show a material benefit over the current accepted route. Benefits may include higher task success, lower regression escapes, fewer false human requests, lower cost, or lower latency. A quality gain that is smaller than the measurement uncertainty is not an adoption result.

## Data boundary

Run records must not contain:

- credential values or Secret values;
- private model transcripts or hidden reasoning;
- personal data;
- production payloads;
- unbounded logs;
- raw environment dumps.

Store only bounded identifiers, aggregate counts, fixed environment facts, and exact-head evidence needed to reproduce or audit the trial.

## Example

```json
{
  "schema_version": 1,
  "run_id": "baseline.task-001.trial-1",
  "task_id": "task-001",
  "foundation_sha": "1111111111111111111111111111111111111111",
  "candidate_sha": "2222222222222222222222222222222222222222",
  "harness": "github-direct-v1",
  "adapter": "github-direct",
  "model": null,
  "trial": 1,
  "environment": {
    "os": "ubuntu-24.04",
    "architecture": "x86_64",
    "python": "3.12.0",
    "cpu_count": 2,
    "memory_mib": 4096,
    "timeout_seconds": 900,
    "network_mode": "disabled",
    "tool_versions": {"git": "2.50.0"}
  },
  "started_at": "2026-08-05T00:00:00Z",
  "finished_at": "2026-08-05T00:01:30Z",
  "outcome": "passed",
  "failure_class": null,
  "metrics": {
    "task_success": true,
    "first_pass_success": true,
    "scope_violation_attempts": 0,
    "regression_escapes": 0,
    "human_action_requests": 0,
    "confirmed_human_actions": 0,
    "false_human_action_requests": 0,
    "iterations": 1,
    "elapsed_seconds": 90,
    "github_api_requests": 12,
    "actions_minutes": 1.5,
    "estimated_cost_usd": null,
    "handoff_recovery": "not_applicable"
  },
  "checks": [
    {
      "name": "CI",
      "source": ".github/workflows/ci.yml",
      "required": true,
      "conclusion": "success",
      "head_sha": "2222222222222222222222222222222222222222"
    }
  ],
  "unresolved_review_threads": 0
}
```

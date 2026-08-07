# Controlled coding-agent experiment plans and reports

## Purpose

Phase D compares experimental orchestration against the accepted GitHub-direct baseline. The comparison must use the same public task identities, Foundation SHA, environment facts, trial matrix, and `AGENT_EVAL_RUN` evidence contract. This layer defines comparison structure only: it does not invoke a model or provider and does not recommend adoption.

## Canonical experiment plan

`scripts/agent_eval_experiment_contract.py` parses one canonical JSON plan. A plan binds:

- an experiment identity;
- the exact evaluation suite ID, version, catalog digest, and Foundation SHA;
- one environment-profile label;
- a sorted exact task list;
- one bounded trial count applied to every planned task;
- sorted experiment arms;
- a sorted subset of interruption tasks used for handoff comparison.

Each arm has an `arm_id`, one role (`baseline`, `planner`, or `evaluator`), a harness identity, an adapter identity, and an optional model label. Exactly one baseline is required. Roles and execution identities are unique so one run cannot ambiguously belong to multiple arms.

The plan parser rejects unknown members, duplicate JSON members, non-canonical JSON, unsafe identities, malformed SHAs or digests, invalid counts, unsorted or duplicate tasks/arms, interruption tasks outside the task set, duplicate roles, and duplicate harness/adapter/model execution identities.

## Aggregate report

`scripts/agent_eval_report.py` accepts a parsed experiment plan and bounded individual run records. Every supplied record must first pass the authoritative `scripts/agent_eval_contract.py` parser.

A run is accepted into the report only when:

- its Foundation SHA matches the plan;
- its task and trial belong to the plan;
- its harness/adapter/model identity matches exactly one arm;
- its arm/task/trial cell has not already been supplied;
- its environment facts exactly match every other supplied run in the report.

Missing cells are reported separately from supplied `infra_error` runs. Infrastructure-invalid runs occupy their planned cell but are excluded from performance-rate denominators so missing evidence, invalid infrastructure, and observed task failure remain distinct.

## Derived arm metrics

For each arm the report exposes immutable observations:

- supplied, valid, infrastructure-invalid, and missing run counts;
- task-success and first-pass rates with Wilson 95% intervals;
- median iterations and elapsed seconds;
- summed scope violations and regression escapes;
- summed human-action requests, confirmed human actions, and false human-action requests;
- summed GitHub API requests and Actions minutes;
- count and median of non-null observed cost values;
- handoff resumed and failed counts.

A zero observed cost is different from a missing cost. Missing cost remains absent from the observed-cost distribution.

## Interpretation boundary

The report intentionally does not calculate an adoption decision, rank providers/models, or pool different environments. A complete-looking score is not sufficient evidence to change the default Foundation route. Phase D must interpret effect size together with intervals, missing/invalid trials, resource consumption, regression escapes, scope violations, and false human-action requests.

The GitHub-direct route remains authoritative until a later accepted Issue records a controlled comparison and an explicit adoption decision.

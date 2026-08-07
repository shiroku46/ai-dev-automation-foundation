# Sealed agent-visible evaluation trial requests

## Purpose

A controlled coding-agent trial must not receive the grader implementation, expected outcome label, known solution, or unrelated Foundation repository context. `scripts/agent_eval_trial_request.py` creates the minimal provider-neutral request that crosses from the trusted evaluation controller to a fresh trial agent.

This contract does not execute an agent, materialize a custom sandbox, select a provider, or change ordinary Foundation runtime behavior.

## Trusted inputs

`build_agent_trial_request(plan, suite, arm_id, task_id, trial)` accepts:

- a parsed canonical Phase D experiment plan;
- a suite already loaded through `load_evaluation_suite`, which has validated manifest and fixture/grader bundle identities without executing them;
- one planned arm ID;
- one planned task ID;
- one one-based planned trial number.

The builder fails closed when suite/catalog/Foundation identity differs from the plan, a task is not present in both plan and suite, the arm or trial is invalid, the task trial count or environment profile differs from the plan, a manifest/fixture identity is inconsistent, or protected authorization metadata is incomplete.

## Agent-visible fields

The canonical request contains only information required to perform the trusted task:

- experiment and plan identity;
- selected arm role and harness/adapter/model labels;
- suite, catalog, Foundation, task, manifest, and trial identity;
- environment-profile label;
- trusted task Issue title and body;
- risk tier, allowed paths, prohibited effects, and required check names;
- protected authorization metadata when the task is protected;
- fixture-bundle identity and a sorted file index containing only path, size, digest, and executable flag.

The fixture file bytes are materialized separately by a later trusted workspace boundary. The request itself carries only their immutable public identity.

## Information that stays runner-side

The request intentionally does not expose:

- grader root or source;
- grader digest, entrypoint, runtime, or timeout;
- expected completion class;
- expected human-only reason code;
- known solution content or regression-solution helpers;
- unrelated repository-root files;
- host environment dumps, credential/Secret values, raw transcripts, or hidden reasoning.

The grader remains a trusted post-execution observer. Preventing the candidate agent from reading grader code or checked-in known-solution test helpers is necessary for a meaningful controlled comparison.

## Determinism and identity

`serialize_agent_trial_request` emits sorted canonical UTF-8 JSON and enforces a 262,144-byte upper bound. `agent_trial_request_sha256` is the deterministic request identity. Identical plan/suite/arm/task/trial inputs produce byte-identical requests; changing the arm, task, or trial changes that identity.

The request is an immutable data object. It is evidence about what the agent was allowed to see, not proof that an agent ran or that a task succeeded.

## Next boundary

A later Phase D increment may materialize only the selected fixture into an isolated disposable workspace and measure an execution attempt. That later step must preserve this sealed request boundary and must not silently mount the Foundation source tree, grader tree, test known-solution helpers, host credentials, or unrelated GitHub state into the agent-visible workspace.

# Coding-agent evaluation task and grader contract

## Purpose

The Foundation evaluation suite needs immutable tasks before it can compare a GitHub-direct baseline with later planner, evaluator, repository-discovery, workspace, or provider variants. This contract defines one task manifest and its deterministic grader identity. It does not add benchmark fixtures, execute an agent, select a provider, or change ordinary Foundation behavior.

`scripts/agent_eval_task_contract.py` is the authoritative fail-closed parser. `docs/AGENT_EVAL_TASK.schema.json` is the public structural Schema. Cross-field checks and canonical-byte requirements remain authoritative in the Python parser.

## Canonical manifest

A task manifest is UTF-8 JSON with these properties:

- no more than 65,536 bytes;
- exactly the documented members and no duplicate JSON names;
- no `NaN`, `Infinity`, non-integer numeric substitutes, or unknown members;
- keys sorted lexicographically;
- compact separators with no insignificant whitespace;
- no trailing newline;
- non-ASCII text retained as UTF-8 rather than rewritten as escape sequences.

The parser reconstructs canonical JSON using `sort_keys=True`, compact separators, and `ensure_ascii=False`, then requires an exact byte-for-byte match. Its `manifest_sha256` is the lowercase SHA-256 of those exact canonical bytes.

This digest identifies the complete task definition. Editing the Issue text, allowed paths, grader, trial count, or any other field creates another manifest identity and requires a task-version decision.

## Task identity

Every manifest binds:

- a lowercase deterministic `task_id`;
- a positive `task_version`;
- one bounded `category`;
- one risk tier: `low`, `standard`, or `protected`;
- one fixed environment-profile identity;
- a trial count from 1 through 100;
- unique lowercase tags.

The initial categories cover bounded bug fixes, tests, multi-file changes, scope traps, protected boundaries, stale evidence, provider unavailability, genuine human-only cases, and interrupted handoff resumption. Adding a category is a contract change, not free-form task metadata.

## Immutable fixture bundle

`fixture_bundle` records:

- lowercase SHA-256 of the immutable fixture archive or canonical bundle;
- positive file count;
- positive uncompressed byte count.

The digest identifies bytes, while the count and uncompressed size provide bounded extraction checks. A runner must independently verify all three before making a workspace available. The task contract does not permit production repositories, Secrets, personal data, or unbounded fixture downloads.

## Immutable grader contract

`grader` records:

- lowercase grader-bundle SHA-256;
- runtime from the fixed runtime enum;
- exact entrypoint below literal `grader/`;
- positive timeout no greater than 86,400 seconds;
- network mode `disabled` or `allowlisted`.

The entrypoint is a repository-relative exact path. Absolute paths, Windows drive paths, backslashes, traversal, `.git`, repeated separators, and glob metacharacters fail validation. A grader is evidence generation, not an authorization source. It must not receive repository write credentials, deployment credentials, OIDC, or Foundation merge authority.

## Owner-style Issue contract

The manifest includes a bounded Issue title and body. These are the task specification presented to the candidate route. The title is a single trimmed line. The body may contain LF newlines but no carriage returns or prohibited controls.

The Issue text must be self-contained enough for the route under evaluation. Hidden chat history, private transcripts, and raw reasoning are not part of the task contract.

## Exact allowed paths

`allowed_paths` contains one or more unique entries. Each entry is either:

- an exact repository-relative path; or
- one bounded trailing `/**` scope pattern.

At most one entry may use trailing `/**`. Arbitrary globs, mid-path `**`, traversal, empty path segments, `.git`, absolute paths, drive paths, and backslashes are rejected. The manifest only describes task scope; the existing trusted owner Issue and coordinator controls remain authoritative during a real Foundation run.

## Prohibited effects and required checks

Every task lists at least one prohibited effect and at least one required check. Both lists are bounded and case-insensitively unique.

Examples of prohibited effects include workflow changes, Secret access, deployment, billing, repository-setting mutation, or touching paths outside the task scope. Required checks are the exact check names the task expects before a successful run record can be accepted.

Task-specific graders supplement rather than replace Foundation-native CI, product-owned checks, exact-head review, scope enforcement, or unresolved-thread checks.

## Protected authorization

A `protected` task must contain a complete `protected_authorization` object:

- trusted GitHub actor;
- evidence source: Issue body, Issue comment, or Pull Request review;
- fixed uppercase authorization marker;
- `expected_head_required: true`.

A `low` or `standard` task must set `protected_authorization` to `null`. The object describes evidence the protected-boundary task will test. It never grants authority by itself, and a candidate branch cannot self-authorize.

## Expected completion and human-only boundary

`expected_completion_class` is one of:

- `change_required`;
- `no_change_required`;
- `human_action_required`.

Only `human_action_required` may carry `expected_human_action_reason`, and it must carry exactly one of the Foundation's audited reason codes:

- account-level repository creation when the required UI is unavailable;
- credential-provider UI interaction;
- disconnected integration reconnection UI interaction.

All other completion classes require a null reason. Provider quota, technical failure, test failure, missing implementation, routine approval, or uncertainty are not human-only reasons. This makes genuine human-only tasks and false-human-request traps measurable with the run-record contract.

## Sensitive-content boundary

The parser rejects high-confidence credential, private-key, token, password-assignment, hidden-analysis, chain-of-thought, and private-reasoning markers anywhere in the canonical manifest. Task fixtures and grader bundles require their own independent content controls; passing this manifest parser is not permission to include sensitive payloads elsewhere.

## Deterministic validation order

A consumer should:

1. read the manifest as bounded bytes;
2. reject invalid UTF-8 and prohibited sensitive markers;
3. parse with duplicate-member and non-standard-number rejection;
4. verify exact top-level and nested members;
5. reconstruct and compare canonical bytes;
6. validate identities, enums, limits, digests, paths, and collections;
7. enforce protected-risk and human-only cross-field rules;
8. retain the immutable parsed record and exact `manifest_sha256`;
9. independently verify fixture and grader bundle bytes before execution.

## Example

```json
{"allowed_paths":["src/parser.py","tests/**"],"category":"bug_fix","environment_profile":"ubuntu-24.04-python3.12-v1","expected_completion_class":"change_required","expected_human_action_reason":null,"fixture_bundle":{"file_count":12,"sha256":"1111111111111111111111111111111111111111111111111111111111111111","uncompressed_bytes":4096},"grader":{"entrypoint":"grader/grade.py","network_mode":"disabled","runtime":"python3.12","sha256":"2222222222222222222222222222222222222222222222222222222222222222","timeout_seconds":900},"issue":{"body":"## Goal\n\nRepair the bounded parser and preserve exact-path scope.","title":"[Eval] Repair bounded parser behavior"},"prohibited_effects":["No workflow changes","No credential access"],"protected_authorization":null,"required_checks":["CI","Unit Tests"],"risk_tier":"standard","schema_version":1,"tags":["parser","bounded","regression"],"task_id":"foundation.task-001","task_version":1,"trial_count":3}
```

The example is compact because canonical bytes are the manifest identity. Documentation may pretty-print illustrative fragments, but stored manifests must use the exact canonical form.

## Non-goals

This contract does not:

- add the initial 30 evaluation fixtures;
- execute product or repository code;
- contact a provider or network service;
- grant access to a Secret, token, OIDC assertion, deployment, billing, or repository setting;
- change Queue, Supervisor, collision, merge, security-policy, Bootstrap, or generated-target behavior;
- claim that a model, harness, planner, evaluator, or workspace is better.

## Rollback

The contract is additive and inactive unless explicitly imported. Revert its merge commit to remove it. Existing Foundation and product-repository behavior remains unchanged.

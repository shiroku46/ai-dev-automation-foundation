# Coding-agent task grader invocation and result contract

## Purpose

The accepted evaluation task contract binds a grader bundle, runtime, entrypoint, timeout, and network mode. The accepted run-record contract binds final trial evidence. This contract defines the narrow interface between them so every public task grader is invoked consistently and returns comparable, identity-bound evidence.

This increment defines contracts and deterministic parsing only. It does not execute an agent, candidate workspace, grader, fixture, provider, product repository, workflow, or network request.

`scripts/agent_eval_grader_contract.py` is authoritative for canonical bytes, cross-field rules, expected identity, sensitive-content rejection, and exit/result agreement. `docs/AGENT_EVAL_GRADER_RESULT.schema.json` is the public structural Schema.

## Deterministic invocation

A runner selects the interpreter from the validated task manifest and constructs an argument vector, never an interpolated shell command:

- `python3.12`: `python3.12 <entrypoint> --workspace <workspace> --result <result>`
- `python3.13`: `python3.13 <entrypoint> --workspace <workspace> --result <result>`
- `node20`: `node <entrypoint> --workspace <workspace> --result <result>`
- `bash`: `bash <entrypoint> --workspace <workspace> --result <result>`

The entrypoint is the exact validated path below `grader/`. The working directory is the validated grader-bundle root. The workspace and result arguments are absolute paths. The result path must remain outside the disposable candidate workspace.

The runner, not the grader, enforces the manifest timeout and network mode. It provides no repository write credential, Secret, OIDC assertion, deployment or billing credential, merge authority, production data, personal data, private model transcript, or hidden reasoning. The candidate workspace is disposable and its candidate SHA is captured before grader execution.

`build_grader_command` validates the fixed runtime, entrypoint, and absolute path boundaries and returns an immutable argument tuple. It never starts a process and never returns a shell string.

## Runner-owned identity environment

A grader must receive result identity from runner-controlled non-secret process variables rather than deriving identity from candidate workspace contents or embedding bundle identity inside the grader. Embedding the grader digest in the grader bundle would change the bundle digest and create a self-referential identity, so the bundle must not contain a hard-coded copy of its own digest.

`build_grader_identity_environment` first validates the caller-supplied `GraderResultExpectation` through the same fail-closed identity validation used by result parsing. It then returns an immutable, deterministically ordered collection containing exactly these seven string variables:

- `AI_DEV_EVAL_TASK_ID`
- `AI_DEV_EVAL_TASK_VERSION`
- `AI_DEV_EVAL_MANIFEST_SHA256`
- `AI_DEV_EVAL_GRADER_SHA256`
- `AI_DEV_EVAL_FOUNDATION_SHA`
- `AI_DEV_EVAL_BASE_SHA`
- `AI_DEV_EVAL_CANDIDATE_SHA`

`AI_DEV_EVAL_TASK_VERSION` is the validated positive task version rendered as its decimal string. Every other value exactly matches the corresponding validated expectation field.

The runner creates a minimal allowlisted process environment for the grader and adds only these evaluation identity variables. It must not copy or merge arbitrary host, candidate, repository, CI, credential, token, Secret, OIDC, deployment, billing, or user environment variables into the grader process. Any runtime-specific variables required to start the fixed interpreter must be explicitly allowlisted by the runner rather than inherited wholesale.

The grader copies these seven runner-owned identity values into the atomic canonical result. It does not derive them from workspace files, Git state, environment discovery, or grader-bundle contents. The runner still compares the returned result against its independently held `GraderResultExpectation`, so stale, cross-task, cross-bundle, or cross-SHA evidence remains invalid.

## Result-file boundary

The caller controls the result location. A grader writes one UTF-8 JSON result atomically, using a temporary sibling and replacement rather than exposing a partially written accepted result. The runner applies the 65,536-byte result limit before parsing.

Stdout and stderr are bounded diagnostics only. They are not accepted grader evidence and must not be copied into the canonical result or run record without a separate bounded evidence rule.

## Canonical result identity

Every result binds:

- integer schema version `1`;
- deterministic task ID and positive task version;
- exact task-manifest and grader-bundle SHA-256 values;
- exact lowercase Foundation, base, and candidate Git SHAs;
- overall outcome `passed` or `failed`;
- one or more sorted, unique check records;
- a bounded human-readable summary.

The parser reconstructs compact JSON with lexicographically sorted keys, UTF-8 non-ASCII text retained, no insignificant whitespace, and no trailing newline. The input must match those bytes exactly. `result_sha256` is the SHA-256 of the exact canonical input bytes and is returned by the parser rather than trusted from grader-controlled JSON.

A caller supplies `GraderResultExpectation` containing all seven task, bundle, and SHA identity fields. Any stale, cross-task, cross-manifest, cross-grader, cross-Foundation, cross-base, or cross-candidate result fails validation.

## Check records

Each check contains:

- lowercase deterministic `check_id`;
- `passed` or `failed` outcome;
- a trimmed single-line message of at most 500 characters;
- zero through 32 unique exact candidate-workspace-relative evidence paths.

Checks are sorted by `check_id`, unique, and limited to 64 records. Evidence paths reject absolute and drive paths, traversal, backslashes, repeated separators, `.git` in any ASCII case, controls, and glob metacharacters. Case-ambiguous duplicate evidence paths fail closed.

An overall `passed` result requires every check to pass. An overall `failed` result requires at least one failed check. A grader result does not contain a score, confidence, cost, duration, infrastructure classification, or model-quality claim. Those belong to controlled run records or later aggregate reports.

## Exit semantics

`validate_grader_process_result` classifies only process/result agreement and does not execute a process:

- exit `0` requires a parsed `passed` result;
- exit `1` requires a parsed `failed` result;
- missing or malformed results, identity mismatch, exit/result disagreement, timeout, signal termination, and any other exit code are grader or infrastructure failures.

Infrastructure failures must not be converted into candidate task failures. A result file that says `failed` does not become trustworthy when the process exits `2`, and a `passed` result is rejected when the process exits `1`.

## Sensitive-content boundary

Canonical result bytes reject high-confidence credential, private-key, token, password-assignment, hidden-analysis, chain-of-thought, and private-reasoning markers. Results must not contain raw logs, environment dumps, production payloads, personal data, or model transcripts. Evidence paths and bounded messages identify the deterministic observation without embedding sensitive contents.

## Validation order

A runner should:

1. validate the task manifest and suite/bundle identity;
2. construct exact argv with `build_grader_command`;
3. build the seven-variable identity collection with `build_grader_identity_environment` and add it to a minimal runner-owned allowlisted process environment;
4. enforce timeout, network, credential, and workspace isolation outside the grader;
5. capture bounded diagnostics separately;
6. read the atomic result within the byte limit;
7. parse it with the exact expected identity;
8. validate process exit/result agreement;
9. map valid task evidence into the accepted run record;
10. classify all missing, stale, malformed, or inconsistent evidence as grader/infrastructure failure.

## Example

```json
{"base_sha":"4444444444444444444444444444444444444444","candidate_sha":"5555555555555555555555555555555555555555","checks":[{"check_id":"acceptance","evidence_paths":["src/example.py","tests/test_example.py"],"message":"Acceptance conditions passed.","outcome":"passed"}],"foundation_sha":"3333333333333333333333333333333333333333","grader_sha256":"2222222222222222222222222222222222222222222222222222222222222222","manifest_sha256":"1111111111111111111111111111111111111111111111111111111111111111","outcome":"passed","schema_version":1,"summary":"The deterministic acceptance checks passed.","task_id":"foundation.task-001","task_version":1}
```

## Non-goals and rollback

This contract does not implement a sandbox, execute a task, add public fixtures, create benchmark results, grant credentials, or change ordinary Foundation behavior. Revert the eventual merge commit to remove it; the accepted GitHub-direct route remains unchanged.

# Post-agent workspace delta and scope evidence

## Purpose

After a fresh coding agent mutates a disposable fixture workspace, the trusted controller must measure what changed before it interprets grader output. `scripts/agent_eval_trial_delta.py` inspects the candidate directory without executing it and compares the result only with the sealed request's fixture-file index.

This layer records mutation and scope evidence. It does not run an agent, run the grader, judge task success, or make an adoption decision.

## Candidate snapshot

`inspect_candidate_workspace` recursively inspects one real directory and produces a deterministic bundle index compatible with the evaluation bundle hashing scheme. Unlike a checked-in fixture bundle, the post-agent snapshot may contain zero files so destructive deletion remains observable evidence rather than being misclassified as a missing run.

Inspection rejects:

- symlinks and mount/device escapes;
- hard-linked or non-regular files;
- `.git` path components and unsafe relative paths;
- case-ambiguous paths;
- files or directories that change during inspection;
- per-file, total-byte, or file-count bound violations.

The inspector never executes candidate contents.

## Delta classification

`inspect_agent_trial_delta(request, candidate_workspace)` compares the candidate file index with the sealed baseline index and records sorted paths for:

- additions;
- content modifications;
- deletions;
- executable-bit changes;
- the union of all mutations;
- mutations outside the trusted allowed-path contract.

A path can appear in both content-modified and executable-changed sets, while the mutation union counts that path once.

## Scope semantics

Allowed paths retain the same bounded task semantics used by the evaluation manifest:

- an exact path authorizes only that exact repository-relative path;
- a single suffix form such as `tests/**` authorizes descendants below `tests/`;
- no glob expansion, parent traversal, case folding, or implicit sibling authorization is added by the delta inspector.

A rename is therefore two mutations: deletion of the old path and addition of the new path. Both endpoints must independently be in scope. An out-of-scope mutation is always retained in `scope_violation_paths`; successful in-scope work cannot erase it.

## Deterministic evidence

The returned immutable evidence binds:

- the sealed request SHA256;
- task and trial identity;
- candidate bundle SHA256, file count, and byte count;
- each mutation class;
- mutation and scope-violation counts.

`serialize_agent_trial_delta` emits bounded canonical JSON. This evidence can later feed the authoritative evaluation-run metrics, but it is not itself a run record or grader result.

## Isolation boundary

The baseline comes only from fixture file paths/digests already present in the sealed request. The delta inspector does not read the task grader, Foundation root tests, known-solution helpers, repository credentials, model transcripts, or unrelated GitHub state.

The caller remains responsible for process isolation, agent invocation, grader isolation, timing/resource instrumentation, run-record construction, and workspace disposal.

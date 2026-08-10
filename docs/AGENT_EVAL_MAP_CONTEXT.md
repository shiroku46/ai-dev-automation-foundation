# Map-assisted Phase D trial requests

Issue: #272

## Purpose

A prepared Phase D evaluation session already contains one sealed no-map trial request, a fixture-only agent workspace, and deterministic baseline Git identity stored in separate bare metadata. `scripts/agent_eval_map_context.py` adds an **optional** agent-visible wrapper that pairs that unchanged request with exact-commit map-selected read context.

The ordinary no-map request remains independently usable and byte-identical. This adapter does not invoke a model, provider, grader, network operation, or repository runtime.

## Build boundary

`build_map_assisted_trial_request(plan, suite, prepared_session, seed_paths, ...)` first rebuilds the selected request from the trusted plan/suite and verifies:

- prepared request SHA and canonical request bytes;
- workspace identity shared by the prepared trial and detached Git evidence;
- sealed fixture bundle identity;
- deterministic baseline commit/tree identity;
- zero workspace mutation and zero scope violation before context construction.

The workspace must still equal the original sealed fixture. Map-assisted context is therefore prepared **before** an external agent mutates the fixture.

## Detached repository context

The builder uses the accepted #271 detached adapter against the prepared session's actual workspace and separate bare Git metadata.

It builds:

1. the exact-SHA E1 `RepositoryMap` at the session baseline commit;
2. an E2 `RepositoryContextPackage` from caller-supplied sorted unique seed paths.

The context receives `request.allowed_paths` verbatim as `trusted_allowed_paths`. Dependency, dependent, or impacted-test files may appear as additional **read** context, but they never become writable paths. The builder requires the map and context commit/tree identities to match the prepared baseline evidence.

The workspace is re-inspected after context construction. Any concurrent or prior fixture mutation fails closed.

## Agent-visible wrapper

The canonical wrapper contains exactly five top-level keys:

```json
{
  "mode": "map-assisted",
  "repository_context": {},
  "schema_version": 1,
  "trial_request": {},
  "wrapper_sha256": "..."
}
```

`trial_request` is obtained by decoding the existing canonical `serialize_agent_trial_request` bytes. `repository_context` is obtained from the accepted canonical E2 serialization. Neither object is rewritten with additional authority or execution metadata.

`wrapper_sha256` is SHA-256 over the canonical four-key payload before the digest field is added. The full five-key JSON is also canonical, bounded, compact UTF-8.

## Returned evidence

`MapAssistedTrialRequest` is immutable and retains only agent-safe identity evidence:

- the original `AgentTrialRequest`;
- original canonical request bytes and SHA-256;
- baseline repository commit/tree/map digests;
- the sealed `RepositoryContextPackage` and canonical context bytes;
- canonical wrapper bytes and wrapper SHA-256.

It deliberately does **not** retain the prepared session, workspace path, Git metadata path, Git environment, remote configuration, grader path/source, ground truth, known solution, transcript, hidden reasoning, credentials, or expected completion label.

`serialize_map_assisted_trial_request` revalidates all embedded request/context/digest identities and exact mutation-scope equality before returning bytes.

## Determinism

Identical prepared-session state, seed paths, impact depth, and path limit produce byte-identical context and wrapper identities.

Changing the context seed/depth may change context/wrapper identity while the original no-map request bytes, request SHA, and `allowed_paths` remain unchanged.

Unknown, unsorted, duplicate, or unsafe seed paths retain E1 fail-closed semantics. Moved baseline refs, configured remotes, tampered session evidence, stale tree identity, and any pre-agent fixture mutation are rejected.

## Security and adoption boundary

The wrapper contains read context only. It adds no write grant, no `expanded_allowed_paths`, no `.git` metadata, no credential interface, and no grader or provider execution surface.

This feature remains opt-in experimental infrastructure for controlled Phase D/E comparison. Its existence is not evidence that repository-map context improves outcomes and does not change the accepted GitHub-direct/no-map default route.

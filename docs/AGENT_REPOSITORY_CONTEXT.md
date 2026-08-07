# Sealed map-selected repository read context

## Purpose

Foundation Next Phase E separates **what an agent may read** from **what it may mutate**. `scripts/agent_repository_context.py` builds a deterministic read-context package from an accepted exact-SHA repository map and the bounded impact query introduced by E1.

The package may include dependencies, dependents, and impacted tests outside the trusted mutation scope. Those files are read-only context. The package never derives or widens writable paths.

## Trusted inputs

`build_repository_context_package(repository_root, repository_map, seed_paths, trusted_allowed_paths, ...)` accepts:

- one exact local Git repository;
- an immutable E1 `RepositoryMap`;
- sorted unique exact map paths used as context seeds;
- sorted unique trusted mutation paths supplied by the caller;
- bounded impact depth/path controls.

Before selecting context, the builder verifies the map's canonical digest and rebuilds the exact-SHA map from the repository. A stale HEAD/tree, altered map entry, or changed map digest therefore fails before any context bytes are returned.

## Read selection

The accepted E1 impact query selects three advisory groups:

- seed paths;
- forward local dependency context;
- reverse dependents, including impacted tests.

The package reads only the union of those exact mapped paths. Impact expansion is bounded and fails closed rather than silently truncating.

## Exact committed content

Selected content is read with local `git cat-file blob <sha>` using the blob SHA and byte size already bound by the repository map. Dirty tracked files and untracked working-tree files are never used as source of truth.

For every selected file the package records:

- exact repository-relative path;
- Git blob SHA;
- byte size;
- executable flag;
- committed UTF-8 text content.

The blob bytes are re-hashed using the Git blob-object format before inclusion. Per-file bytes, aggregate content bytes, selected-file count, and final serialized package size are bounded.

The initial sealed context accepts UTF-8 text only. It rejects NUL/prohibited control payloads and conservative credential/private-key/token-like or hidden-reasoning markers. The map can still represent non-text tracked files; E2 simply refuses to place unsafe/non-text bytes into an agent context package.

## Mutation scope remains independent

`trusted_allowed_paths` are caller-supplied authority. E2 validates the same bounded semantics used by evaluation tasks:

- exact paths authorize only that exact mutation path;
- at most one suffix form such as `src/**` may represent a bounded descendant scope;
- parent traversal, `.git`, backslashes, arbitrary globs, and unsafe paths are rejected.

The scope is not expanded against the repository map and is not inferred from dependencies. A package can therefore contain `app.py` or `tests/test_app.py` as read context while `trusted_allowed_paths` remains only `pkg/core.py`.

No `expanded_allowed_paths`, writable-context set, or equivalent grant exists in the package schema.

## Deterministic package identity

The non-self-referential canonical payload contains:

- exact repository commit/tree/map identities;
- impact seed/context/dependent/test paths;
- trusted allowed paths;
- impact depth;
- sorted exact read-file records and committed content.

`package_sha256` is the SHA-256 of that canonical payload. `serialize_repository_context_package` verifies the digest and emits bounded compact sorted UTF-8 JSON.

Identical exact Git state, map, seeds, scope, and impact controls produce byte-identical context packages independent of dirty working-tree changes. Changing the advisory seed/depth changes context identity but never modifies the separately supplied trusted scope.

## Experimental boundary

E2 does not invoke a model/provider, execute repository code, build embeddings, access Git remotes, or change ordinary Foundation runtime behavior. A later E3 controlled comparison may decide whether this extra read context improves development outcomes. Until evidence supports adoption, map-selected context remains optional and GitHub-direct without repository-map context remains valid.

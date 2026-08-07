# Exact-SHA repository map and bounded impact discovery

## Purpose

Foundation Next Phase E tests whether small deterministic repository context improves coding-agent work before considering heavier indexing or semantic retrieval. `scripts/agent_repository_map.py` builds an advisory file/import map from one exact local Git commit and answers bounded dependency-impact queries.

The map is **not** mutation authority. Trusted owner-authored Issue `allowed_paths` remain the only mutation boundary. An impact result may suggest files to read or checks to consider; it never expands scope.

## Exact Git source of truth

`build_repository_map(repository_root, expected_sha)` requires a real Git top-level worktree whose lowercase SHA-1 `HEAD` exactly matches the caller's expected SHA. It uses only local Git object commands:

- `rev-parse --show-object-format`;
- `rev-parse --show-toplevel`;
- `rev-parse HEAD` and `HEAD^{tree}`;
- `ls-tree -r -l -z --full-tree <sha>`;
- `cat-file blob <blob-sha>` for bounded Python blobs.

System and global Git configuration are disabled. The module does not fetch, pull, contact remotes, inspect credentials, or use untracked/dirty working-tree file bytes as mapping evidence. A dirty working tree therefore does not change the map for the same exact commit.

## Tracked-file boundary

The initial experiment accepts only regular tracked blobs with Git modes `100644` or `100755`. It rejects symlink/gitlink/submodule entries instead of following or approximating them. Paths must be bounded UTF-8 repository-relative paths with no control characters, backslashes, parent traversal, `.git` components, duplicate identity, or case ambiguity.

File count, individual blob bytes, total tracked bytes, Git output, Python parse bytes, dependency edges, and serialized map bytes are bounded. Hitting a bound fails closed rather than creating a partial map.

## Python import graph

Python source is read from the committed blob object and parsed with the standard-library `ast` parser. Repository code is never imported or executed.

The map records:

- file path, blob SHA, size, executable flag, and file kind;
- deterministic Python module identity where the path can represent one;
- observed imported module strings;
- resolvable local dependency paths;
- reverse local dependent paths.

Module paths follow normal file/package structure: `pkg/mod.py` maps to `pkg.mod`, while `pkg/__init__.py` maps to `pkg`. Ambiguous local module identities fail closed.

The first import resolver covers:

- `import package.module`;
- `from package.module import name`;
- `from package import module` when the named module is local;
- relative imports resolved from the importing package.

External or unresolved modules remain observations but do not become local dependency edges. A syntactically invalid tracked Python blob fails the map instead of silently removing graph edges.

## Deterministic map identity

Entries are sorted by exact repository path. A canonical payload contains schema version, exact repository commit SHA, commit tree SHA, and all map entries. `map_sha256` is the SHA-256 of that canonical payload. `serialize_repository_map` emits compact sorted canonical JSON containing the payload digest and verifies the digest before serialization.

The map identity depends on exact Git objects and deterministic parsing, not on checkout location.

## Bounded impact query

`discover_repository_impact(repository_map, seed_paths, max_depth=2, max_paths=...)` accepts sorted unique exact paths already present in the map. It walks two deterministic graphs independently:

- forward local dependencies → advisory `context_paths`;
- reverse local dependents → advisory `dependent_paths`.

The result also identifies impacted test paths from the seed/context/dependent union. Depth and total expanded paths are bounded. Expansion that would exceed the configured bound fails closed instead of truncating and presenting incomplete evidence.

The returned object intentionally contains no `allowed_paths`, writable-path list, authorization token, or scope expansion. A caller must keep the trusted Issue mutation contract separate.

## Experimental adoption boundary

Phase E will later compare map-assisted context with no-map trials using the same controlled evaluation framework. The repository map remains opt-in unless measured evidence shows useful improvement without increasing scope violations, false human requests, latency, or resource consumption. No embeddings, vector database, model-generated repository summary, or production/private repository indexing is part of E1.

# Detached exact-SHA repository map and context

Issue: #271

## Purpose

Phase D evaluation workspaces deliberately keep Git metadata outside the agent-visible fixture directory. `scripts/agent_repository_detached.py` adapts the accepted E1 repository-map and E2 sealed read-context contracts to that layout without changing either ordinary-worktree API.

The adapter reads only local Git objects and refs from a separate bare metadata directory. It never creates `.git` in the workspace, executes repository code, contacts a remote, or expands mutation scope.

## Required layout

Both inputs must be real, non-symlink directories and must be disjoint:

- `repository_root` — the agent-visible workspace;
- `git_metadata_dir` — separate bare Git metadata.

The workspace must not contain `.git`, neither boundary may be nested inside the other, and `.git` path components are rejected. The metadata repository must use SHA-1 objects, be bare, and have no configured Git remote.

## Exact commit anchoring

`build_detached_repository_map(repository_root, git_metadata_dir, expected_sha)` requires the exact commit object to exist and requires the active local identity to resolve to that SHA.

For Phase D metadata, `refs/heads/baseline` is the anchor. For an ordinary bare copy without that ref, `HEAD` is the anchor. If both resolve, both must identify the same expected commit. A moved ref, stale expected SHA, changed tree, configured remote, or non-bare metadata fails closed.

All object/ref reads use local Git with explicit `--git-dir` and `--work-tree` where a work-tree-aware object read is appropriate. System/global Git configuration is disabled and only a small non-credential environment is inherited.

## Map parity with E1

The detached adapter preserves the E1 rules for:

- tracked-path validation and case ambiguity;
- regular modes only (`100644` / `100755`);
- file/byte/output bounds;
- Python UTF-8 AST parsing without import or execution;
- local dependency and reverse-dependent edges;
- exact commit/tree/blob identity;
- canonical `RepositoryMap` serialization and digest.

The returned object is the accepted `RepositoryMap` type. For identical commit/tree objects, the detached result is byte-identical in identity to `build_repository_map` on an ordinary worktree.

Dirty tracked bytes and untracked files in the detached workspace are irrelevant because all tracked content is read from the bare object database.

## Context parity with E2

`build_detached_repository_context_package(...)` revalidates the supplied map against the same detached boundary and then applies the accepted E1 impact query plus E2 trusted-scope, UTF-8 text, sensitive-content, file-count, byte, and serialization rules.

Selected files are read with `git cat-file blob` from the detached metadata object database only. The returned object is the accepted `RepositoryContextPackage` type, including the same canonical package digest as the ordinary-worktree E2 builder for identical map, seeds, scope, depth, and committed bytes.

Dependencies, dependents, and tests may be included as read context while `trusted_allowed_paths` remains exactly caller-supplied authority. No writable-path expansion field exists.

## Read-only invariants

The adapter performs no Git mutation command. It does not create refs, indexes, objects, remotes, commits, worktrees, or `.git` directories. It rechecks the exact ref/tree identity before returning so a concurrent moved-ref boundary fails closed.

The adapter exposes no metadata path or Git environment in `RepositoryMap` / `RepositoryContextPackage`, and it has no credential, Secret, OIDC, network, model/provider, grader, Queue, Supervisor, Bootstrap, or deployment interface.

## Experimental boundary

This adapter only makes the already accepted E1/E2 context semantics available to Phase D's isolated workspace layout. Repository-map context remains opt-in. Issue #272 may compose this adapter with a prepared Phase D request, but no map-assisted route becomes default without controlled evaluation evidence.

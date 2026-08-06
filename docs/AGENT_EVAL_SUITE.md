# Coding-agent evaluation suite catalog and directory bundles

## Purpose

The Foundation evaluation program needs reproducible public tasks before it can compare a GitHub-direct baseline with later handoff, planner, evaluator, repository-discovery, workspace, or provider variants. The accepted task contract in `AGENT_EVAL_TASK.md` binds one task to immutable fixture and grader identities. This contract defines how a suite inventories those task manifests and how checked-in fixture and grader directories produce the bound identities.

This increment is validation only. It does not execute an agent, grader, fixture, provider, product repository, workflow, or network request. It does not add the initial 30 tasks or claim that any harness is better.

`scripts/agent_eval_suite_contract.py` is authoritative for canonical bytes, ordering, path safety, filesystem validation, and cross-file checks. `docs/AGENT_EVAL_SUITE.schema.json` is the public structural Schema.

## Canonical catalog

A catalog is UTF-8 JSON with:

- no more than 1,048,576 bytes;
- exactly the documented members and no duplicate names;
- no `NaN`, infinity, floating substitutes, or unknown members;
- keys sorted lexicographically;
- compact separators and no insignificant whitespace;
- non-ASCII text retained as UTF-8;
- no trailing newline.

The exact canonical bytes are hashed with SHA-256 to produce `catalog_sha256`. Any task addition, path move, digest change, ordering change, or Foundation-SHA change creates a new catalog identity and requires a suite-version decision.

## Catalog identity

Every catalog binds:

- deterministic lowercase `suite_id`;
- positive `suite_version`;
- exact lowercase 40-character `foundation_sha`;
- `task_count` from 1 through 1,000;
- a task list sorted by `(task_id, task_version)`.

Each task entry binds:

- task ID and positive task version;
- one canonical manifest file below `tasks/` ending in `.json`;
- exact manifest SHA-256;
- one checked-in fixture directory below `fixtures/`;
- one checked-in grader directory below `graders/`.

Task identities and all three path classes must be unique. Fixture and grader roots must not overlap another root in the same class. Paths are compared case-insensitively for ambiguity even on a case-sensitive host.

## Literal local paths

Catalog and bundle paths are repository-relative forward-slash paths. Validation rejects:

- absolute and drive paths;
- `.` or `..` segments;
- empty or repeated separators;
- backslashes;
- `.git` segments in any ASCII case;
- glob metacharacters;
- paths outside their required `tasks/`, `fixtures/`, or `graders/` prefix.

The caller supplies a local suite root. The root and every referenced component are inspected with `lstat`; symlinks are rejected before resolution, and the final resolved object must remain below the supplied root.

## Directory-bundle identity

`inspect_directory_bundle` recursively inspects a real local directory without importing or executing its contents. It accepts regular files and directories only. It rejects:

- a symlink root or symlink member;
- hard-linked files with a link count other than one;
- sockets, devices, FIFOs, or other non-regular entries;
- unsafe, duplicate, or case-ambiguous member paths;
- an empty bundle;
- more than 100,000 files;
- a file above 1 GiB;
- total uncompressed content above 10 GiB.

For each regular file, the validator records:

- its path relative to the bundle root;
- exact byte count;
- lowercase SHA-256 of its bytes;
- a normalized Boolean indicating whether any executable bit is set.

The records are sorted by path and serialized as canonical JSON:

```json
{"files":[{"executable":false,"path":"src/example.py","sha256":"<64 lowercase hex>","size":123}],"schema_version":1}
```

The SHA-256 of that canonical index is the directory-bundle SHA-256. The digest binds contents, paths, sizes, ordering, and executable state while remaining independent of timestamps, owner IDs, group IDs, checkout location, or directory metadata.

Empty directories are not part of the identity because Git does not preserve them. Every accepted bundle therefore contains at least one regular file.

## Cross-file validation

`load_evaluation_suite` performs the following without execution:

1. parse the exact canonical suite catalog;
2. establish a real, non-symlink suite root;
3. read each manifest within the accepted task-manifest byte limit;
4. verify manifest path, SHA-256, task ID, and task version;
5. parse the manifest through `agent_eval_task_contract.py`;
6. inspect the fixture directory and compare digest, file count, and uncompressed bytes with the task manifest;
7. inspect the grader directory and compare its digest with the task manifest;
8. verify that the manifest's exact grader entrypoint exists as a regular file below the grader root;
9. return frozen catalog, task, and bundle records.

A grader entrypoint such as `grader/grade.py` is interpreted relative to that task's `grader_root`. Its existence is checked, but it is never imported, invoked, or granted credentials.

## Example catalog

```json
{"foundation_sha":"22913253863025493cb48f9b40949858cdce6d93","schema_version":1,"suite_id":"foundation.initial","suite_version":1,"task_count":1,"tasks":[{"fixture_root":"fixtures/foundation.task-001","grader_root":"graders/foundation.task-001","manifest_path":"tasks/foundation.task-001.json","manifest_sha256":"1111111111111111111111111111111111111111111111111111111111111111","task_id":"foundation.task-001","task_version":1}]}
```

The example digest is illustrative. A stored catalog must contain the actual digest of its exact canonical task-manifest bytes.

## Security and authority boundaries

Bundle validation is not sandboxing and is not permission to execute checked-in contents. Candidate code, candidate graders, task manifests, and bundle digests do not authorize merge or protected changes. Existing trusted Issue scope, proposed-branch credential isolation, product-owned checks, exact-head CI, coordinator review, unresolved-thread checks, and expected-head merge protection remain authoritative.

Public fixture content must still satisfy repository export policy and must not contain real credentials, personal data, private keys, deployment material, or hidden model transcripts.

## Rollback

The catalog and directory validator are additive and inactive unless explicitly imported. Revert the eventual merge commit to remove them. Ordinary Foundation and product-repository behavior remains unchanged.

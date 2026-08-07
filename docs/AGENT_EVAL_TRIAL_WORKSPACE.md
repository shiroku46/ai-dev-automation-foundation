# Sealed evaluation trial workspace materialization

## Purpose

A fresh coding-agent trial needs the selected synthetic fixture bytes, but it must not receive the Foundation source tree, grader implementation, known-solution helpers, Git metadata, or host credentials. `scripts/agent_eval_trial_workspace.py` performs one trusted runner-side operation: reproduce exactly the sealed fixture bundle in a newly created disposable directory.

This component is not a sandbox. It does not launch candidate code, block syscalls, configure containers, provide network isolation, or define the later provider-neutral workspace runtime planned for Foundation Next Phase F.

## Inputs and trust boundary

`materialize_agent_trial_workspace(request, suite, suite_root, destination)` requires:

- an immutable sealed `AgentTrialRequest`;
- the same suite loaded through `load_evaluation_suite`;
- the local checked-in suite root containing the validated public fixture;
- a destination path whose parent already exists and whose final directory does not yet exist.

The materializer revalidates request suite/catalog/Foundation/task/manifest/fixture identities against the loaded suite. It does not trust the request as authority for a different suite or fixture.

## Source revalidation

Immediately before copying, the selected fixture root is resolved below the supplied suite root and inspected through the existing deterministic directory-bundle contract. The observed SHA256, file count, uncompressed byte count, sorted file paths, sizes, file digests, and executable flags must exactly equal the sealed request.

Each source path is then read without following a final symlink. The materializer rejects unsafe path components, links, mount/device escapes, non-regular or multiply linked files, changed metadata, size growth, and digest mismatch.

An unexpected source file changes the bundle identity and stops materialization before the destination is created.

## Destination behavior

The destination must not pre-exist. The materializer creates it itself and writes only request-indexed files. Parent directories are created under that new root; each file is written to a temporary sibling, receives a deterministic executable or non-executable mode, and is atomically renamed into place.

After all files are written, the complete destination is re-inspected. The final bundle identity and complete file index must exactly equal the request fixture identity. On any copy or final-verification failure, the newly created disposable destination is removed.

Returned immutable evidence records:

- resolved destination path;
- sealed request SHA256;
- final fixture SHA256;
- file count;
- uncompressed byte count.

## What is intentionally absent

Only the selected fixture bundle may appear in the workspace. The materializer never copies:

- the task grader directory or grader metadata;
- Foundation `scripts/**`, root `tests/**`, or documentation;
- `tests/test_agent_eval_initial_suite.py` or other known-solution helpers;
- the Foundation `.git` directory;
- host environment variables, credentials, Secrets, OIDC material, or transcripts.

Fixture-local `.github/**` and `.ai-dev/**` files are permitted when their paths and digests are part of the validated public fixture itself. They remain inert data at this stage because this component never executes fixture contents.

## Lifecycle boundary

The caller owns later process isolation, agent invocation, candidate mutation, evidence capture, and disposal. A materialized directory proves only that the agent-visible starting bytes matched the sealed fixture; it is not evidence that an agent ran, that network isolation existed, that a candidate was safe, or that the grader passed.

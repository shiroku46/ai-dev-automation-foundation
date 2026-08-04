# Public workflow security model

| Context | Code source | Permissions | Secrets / OIDC |
|---|---|---:|---:|
| Fork or same-repository Pull Request checks | exact Pull Request head | `contents: read` | none |
| GitHub coordinator Supervisor | current default branch only | `actions: read`, `issues: read`, bounded `contents: write` / `pull-requests: write` | none |
| Optional Claude implementation | immutable authorized base plus default-branch workflow | repository read, Issue/PR read, OIDC for the provider action | one optional Claude credential |
| Optional candidate verification | immutable packaged candidate bytes | `contents: read` | none |
| Optional candidate publication | current default-branch workflow and verified artifact | bounded Git Data / branch / Draft PR write | no provider Secret or OIDC |
| CI reconciliation compatibility | current default branch | `actions: read`, `contents: read` | none |

## GitHub coordinator boundary

The ordinary and sufficient development route is GitHub-direct. The Supervisor never checks out or executes Pull Request code. It reads fixed same-repository GitHub APIs and fails closed unless all of the following are stable on one exact head:

- one open trusted owner-authored source Issue;
- exact or bounded allowed paths, including previous rename paths;
- independent protected authorization for every protected path;
- no overlapping live Pull Request path set;
- candidate `CI` and `Unit Tests` workflow blobs equal the current default-branch definitions;
- successful native Pull Request runs belonging to the exact repository, Pull Request, workflow identity, and head SHA;
- one clean trusted immutable coordinator marker for low/standard work, or clean scope/security and correctness/race markers for protected work;
- zero unresolved review threads;
- no `ai-no-merge` hold;
- unchanged source authorization, default branch, changed paths, collision state, check evidence, review evidence, PR body, head, and mergeability during the final double-read.

A Draft candidate is marked Ready only after those gates pass. The Supervisor then performs a complete fresh evaluation and calls the merge API with the exact expected head SHA. No network query occurs between the final PR identity fetch and the expected-head merge call.

Codex and Claude responses are not implementation-completion, review, readiness, or merge evidence. Provider quota, setup, account, connection, generic output, content-free output, or stale-SHA output cannot block GitHub-direct completion and must not create routine owner work.

## Optional provider boundary

The Claude Queue is an optional helper. It starts only after an owner-authored explicit `/claude-run` request or an explicit owner workflow dispatch. Ordinary Issue creation, Pull Request checks, coordinator review, schedule, and merge supervision do not select a provider.

Before model invocation, the fixed default-branch workflow applies the reviewed edit-only permission-contract preflight. Contradictory command requirements skip the provider with public-safe, non-notifying evidence. The provider action runs in agent mode with `track_progress: false`, persisted checkout credentials disabled, repository content read-only, and a final-turn reserve for path audit and checkpoint preparation.

Provider failure remains non-blocking. `auth_secret` is human-only only when the optional route was explicitly enabled and a separate connected adapter proved one canonical credential UI action is unavoidable. Without both proofs, `human_action_required` remains false and GitHub-direct work continues.

## Candidate and publication isolation

Optional candidate code never executes in a job carrying repository write permission, provider Secrets, or OIDC. The implementation worktree is packaged through no-follow file descriptors after regular-file validation. Changed and untracked paths must remain inside the trusted Issue scope. Read-only verification consumes the immutable artifact before a separate publisher receives bounded Git Data and Draft Pull Request write permission.

The publisher receives no provider credential and does not execute the candidate. It revalidates the exact base, source PR identity when applicable, generated ref, and deterministic Draft PR identity. Existing metadata mismatch fails closed with zero PATCH request; concurrent edits are never auto-restored or overwritten.

## Native evidence and Bootstrap parity

`CI` and `Unit Tests` run on the exact candidate with `contents: read`, no Secrets, no OIDC, and no write permission. Candidate-authored status or custom check evidence is not merge-authorizing. Actions are pinned to immutable commit SHAs.

Bootstrap copies every managed Foundation file byte-for-byte. Generated targets therefore receive the same Supervisor, optional Queue, validator, workflows, templates, startup guidance, and policy. GitHub-only Phase 0 requires connected repository access, enabled Actions/Foundation workflows, and the two Workflow-permissions settings; no provider environment or credential is required.

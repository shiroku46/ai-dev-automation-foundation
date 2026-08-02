# Operating rules

## Authoritative repositories

The public Foundation repository and its public E2E repository are the implementation and acceptance sources of truth. Private predecessor repositories are archives only.

## Mandatory Phase 0 before ordinary flow

Every newly bootstrapped repository must complete the repository-specific procedure in `docs/PROJECT_STARTUP.md` before the first product Issue, `/claude-run`, implementation request, or harmless Bootstrap acceptance exercise.

The coordinator performs all connected inspections first. When no repository-settings API is callable, the owner must complete this one-time GitHub UI action in the exact target repository:

`Settings` → `Actions` → `General` → `Workflow permissions`

1. select **Read and write permissions**;
2. enable **Allow GitHub Actions to create and approve pull requests**;
3. save the setting.

Phase 0 also requires exact-repository GitHub/Codex access, an exact-repository Codex environment, the configured credential name when OAuth is used, enabled Actions and Foundation workflows, and a harmless acceptance candidate proving that branches, Pull Requests, comments/labels, readiness, checks, review, and bounded merge orchestration work.

Do not retry a stalled write-capable workflow or ask the owner to repost commands until this setting has been checked. After acceptance, do not request the setup again unless connected evidence shows that it was reset or the integration is no longer usable.

## Ordinary flow

1. Phase 0 acceptance for the exact repository is already complete and recorded without Secret values.
2. A trusted owner-authored Issue states the goal, acceptance criteria, every allowed changed or renamed path, prohibited effects, and validation.
3. The owner starts the Queue with an exact standalone `/claude-run`, or the trusted default-branch supervisor dispatches it.
4. Claude writes a dedicated branch and Draft Pull Request.
5. Public Pull Request checks execute the exact candidate SHA with `contents: read`, no Secrets, no OIDC, and no write permission.
6. Fixed default-branch trusted checks create GitHub-owned immutable workflow-run and exact job evidence for the same SHA.
7. Fixed native Pull Request workflows create independent exact-head evidence for `CI`, `Unit Tests`, and `E2E Acceptance` when fixed `e2e.yml` exists.
8. Codex independently reviews that exact SHA.
9. The supervisor revalidates provenance, scope, protected authorization, complete trusted and native evidence, Codex and thread state, mergeability, and the current head.
10. The supervisor marks an eligible Pull Request ready and merges through the Merge API with the exact expected head SHA.

A separate human merge click is not required.

No `workflow_dispatch` or `repository_dispatch` event payload may authorize a candidate, source Issue, changed path, workflow, ref, repository, command, or merge.

## Ordinary allowlist and protected authorization

Every changed and renamed path must match an ordinary allowlist declared under a trusted Issue scope heading. Only exact repository-relative paths and bounded suffix patterns such as `tests/**` are accepted. A protected authorization block does not implicitly add paths to the ordinary allowlist.

Protected changes include `.github/**`, `bootstrap/**`, supervisor and security-policy code, permission changes, authentication, repository settings, billing, deployment, production, and destructive data operations. Each protected path must independently appear in both the ordinary allowlist and the protected contract:

```text
## Allowed paths
- .github/workflows/example.yml

<!-- foundation-protected-authorization
category: workflow
paths:
- .github/workflows/example.yml
operation: add one reviewed workflow
prohibited: no secrets, deployment, or repository settings
validation: public CI, tests, exact-SHA Codex review
rollback: revert the merge commit
-->
```

The supervisor fails closed when any current or previous renamed path exceeds the ordinary allowlist or any protected path lacks the stricter contract.

## Native exact-head evidence

Before readiness or merge, the supervisor resolves fixed active default-branch workflow identities for:

- `.github/workflows/ci.yml` / `CI`;
- `.github/workflows/unit-tests.yml` / `Unit Tests`;
- `.github/workflows/e2e.yml` / `E2E Acceptance`, when that fixed workflow exists.

The runtime captures one immutable default-branch commit for the entire native evidence gate. Every required candidate workflow file blob is compared with the corresponding blob from that same default commit. After all workflow metadata, blob, and run queries finish, the default branch is read again; any movement invalidates the complete gate. A mixed old/new default workflow set or candidate-modified workflow can never validate itself.

The supervisor then requires a successful completed `pull_request` run belonging to the exact Pull Request, same repository, fixed workflow identity, and current head SHA. Missing, pending, cancelled, failed, stale-SHA, cross-Pull-Request, wrong-workflow, wrong-repository, candidate-modified-workflow, or candidate-authored status evidence cannot authorize progress. The complete gate is repeated immediately before merge.

When GitHub records automation-authored Pull Request runs as `action_required` before any job starts, connected automation may create one metadata-only commit on the same authorized branch. The new exact head invalidates all prior evidence and must receive fresh native checks, trusted attestations, and Codex review; no person is asked to approve the run.

## Internal stops are durable and non-notifying

Retry exhaustion, no progress, stale or incomplete evidence, blocking review, merge state, ambiguous technical conditions, all-path denial, and protected-path denial are internal automation states. They must never become routine requests for a person to merge, approve, retry, close, resolve a review, change permissions or settings, alter billing, or deploy.

Before persisting an internal stop, the runtime performs the mandatory self-resolution audit against the live exact SHA. It rechecks repository metadata; current Pull Request head and mergeability; complete changed and renamed paths; source Issue trust, ordinary allowlist, and protected authorization; fixed workflow identities and the one stable default snapshot; immutable trusted workflow-run/job evidence; complete native Pull Request workflow evidence; GitHub check evidence; Codex evidence and unresolved threads; collaborator permission; idempotency; and alternative connected recovery paths. It fetches the live Pull Request again after all queries and immediately before any record or disposable close. A failed query or moved head produces no effect.

Internal stop records are sanitized canonical JSON on the fixed non-default branch `automation-internal-stops` at:

```text
automation-stops/pr-<number>/<exact-sha>/<REASON_CODE>.json
```

The record contains `notification: false`, no required human action, the reason, Issue, Pull Request, exact SHA, bounded detail, and connected audit evidence. The canonical public contract is human_action_required: `false`. The path and exact content are the idempotency key. Routine internal stops are never posted as Issue or Pull Request comments and never create or edit routine stop labels. A deliberately disposable negative E2E Pull Request may close only after exact record persistence and another live-head check.

Combined Codex comments and reviews are ordered by immutable event time. Codex no-progress is measured from the immutable trusted exact-SHA request comment authored by `github-actions[bot]`. Native-check and mergeability no-progress use relevant immutable exact-SHA evidence, never Pull Request-wide `updated_at`.

## Human-only notice boundary

Human notice is limited to genuine account/provider/UI prerequisites after connected inspection paths have been attempted. This includes mandatory Phase 0 Workflow-permissions configuration when no callable repository-settings endpoint is available.

A valid notice identifies the exact repository, the connected checks already attempted, the exact UI navigation or local command, the non-secret completion evidence, and the automatic-resumption condition. It must never request Secret values.

For Workflow permissions, the canonical UI action is:

`Settings` → `Actions` → `General` → `Workflow permissions` → **Read and write permissions** + **Allow GitHub Actions to create and approve pull requests** → Save.

Routine technical failures, retry exhaustion, no progress, missing evidence, merge state, path denial, untrusted evidence, unsupported provider assertions, or unresolved ambiguity cannot be converted into repeated human requests. Automation resumes automatically when the audited UI condition changes; a new owner message is not required.

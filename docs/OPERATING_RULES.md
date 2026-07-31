# Operating rules

## Authoritative repositories

This public Foundation repository and its public E2E repository are the implementation and acceptance sources of truth. Private predecessor repositories are archives only.

## Ordinary flow

1. A trusted owner-authored Issue defines the goal, acceptance criteria, every allowed changed or renamed path, prohibited effects, and validation.
2. The owner starts the Queue with an exact standalone `/claude-run`, or the trusted default-branch supervisor dispatches it.
3. Claude writes a dedicated branch and Draft Pull Request.
4. Public Pull Request checks execute the exact candidate SHA with `contents: read`, no Secrets, no OIDC, and no write permission.
5. Fixed default-branch trusted checks create GitHub-owned immutable workflow-run and exact job evidence for the same SHA.
6. Fixed native pull-request workflows create independent exact-head evidence for `CI`, `Unit Tests`, and `E2E Acceptance` when fixed `e2e.yml` exists.
7. Codex independently reviews that exact SHA.
8. The supervisor revalidates same-repository provenance, every changed and renamed path, protected authorization, complete immutable trusted and native evidence, Codex and thread state, mergeability, and the current head.
9. The supervisor marks an eligible Pull Request ready and merges through the Merge API with the exact expected head SHA.

A separate human merge click is not a policy requirement.

## Source Issue allowlist and protected changes

Every changed and renamed path must match an allowlist declared by the trusted source Issue. Bounded patterns such as `tests/**` are supported; an absent, malformed, broad, or nonmatching declaration fails closed.

Protected changes include `.github/**`, `bootstrap/**`, supervisor and security-policy code, permission changes, authentication, repository settings, billing, deployment, production, and destructive data operations. A protected path must be present both in the ordinary Issue allowlist and in an exact protected authorization contract:

```text
<!-- foundation-protected-authorization
category: workflow
paths:
- .github/workflows/example.yml
operation: add one reviewed workflow
prohibited: no secrets, no deployment, no repository settings
validation: public CI, unit tests, exact-SHA Codex review
rollback: revert the merge commit
-->
```

The supervisor fails closed when any current or previous renamed path exceeds the Issue allowlist or any protected path lacks the stricter contract.

## Native exact-head evidence

Before readiness or merge, the supervisor resolves the fixed active default-branch workflow identities for:

- `.github/workflows/ci.yml` / `CI`;
- `.github/workflows/unit-tests.yml` / `Unit Tests`;
- `.github/workflows/e2e.yml` / `E2E Acceptance`, when that fixed workflow exists.

For every required identity, it requires a successful completed `pull_request` run from the same repository on the exact current head SHA. Missing, pending, cancelled, failed, stale-SHA, wrong-workflow, wrong-repository, or candidate-authored status evidence cannot authorize progress. The complete gate is rechecked immediately before merge.

## Internal stops are durable and non-notifying

Retry exhaustion, no progress, stale or incomplete evidence, blocking review, merge state, ambiguous technical conditions, all-path denial, and protected-path denial are internal automation states. They must never become routine requests for a person to merge, approve, retry, close, resolve a review, change permissions or settings, alter billing, or deploy.

Before persisting an internal stop, the runtime performs the mandatory self-resolution audit against the live exact SHA. The audit rechecks repository metadata; current Pull Request head and mergeability; complete changed and renamed paths; source Issue trust, ordinary allowlist, and protected authorization; fixed workflow identities; immutable trusted workflow-run/job evidence; complete native pull-request workflow evidence; GitHub check evidence; Codex evidence and unresolved threads; collaborator permission; idempotency; and alternative connected recovery paths. It fetches the live Pull Request again after all queries and immediately before any record or disposable close. If any query fails or the head moves, no record is written and no stop effect is applied.

Internal stop records are sanitized canonical JSON files on the fixed non-default branch `automation-internal-stops`. Their deterministic path is:

```text
automation-stops/pr-<number>/<exact-sha>/<REASON_CODE>.json
```

The record contains `notification: false`, no required human action, the reason, Issue, Pull Request, exact SHA, bounded detail, and connected audit evidence. The deterministic path and exact content are the idempotency key: an existing matching record creates no duplicate commit, while mismatched content fails closed. Routine internal stops are never posted as Issue or Pull Request comments and never create or edit routine stop labels. A deliberately disposable negative E2E Pull Request may be closed only after exact record persistence and another live-head check.

Combined Codex comments and reviews are ordered by immutable event time before the newest exact-SHA evidence is selected. Codex no-progress is measured from the immutable trusted exact-SHA request comment authored by `github-actions[bot]`. Native-check and mergeability no-progress are measured from relevant immutable exact-SHA evidence, never Pull Request-wide `updated_at` or unrelated activity.

## Human-only notice boundary

A separate fail-closed path may notify a person only for one of these reason codes:

- `HUMAN_ONLY_ACCOUNT_LEVEL_REPOSITORY_CREATION_UI_UNAVAILABLE`
- `HUMAN_ONLY_CREDENTIAL_PROVIDER_UI_REQUIRED`
- `HUMAN_ONLY_DISCONNECTED_INTEGRATION_RECONNECTION_UI_REQUIRED`

A human-only notice requires an exact trusted source Issue, live open same-repository Pull Request, lowercase 40-character current head SHA, concrete connected paths already attempted, concrete impossibility evidence, exact affected targets or provider, one canonical reason-compatible provider UI action, an automatic-resumption condition, and the same mandatory connected self-resolution audit.

Before publication, the runtime persists a sanitized deterministic record at:

```text
automation-stops/pr-<number>/<exact-sha>/<HUMAN_ONLY_REASON>.notice.json
```

That record binds the Issue, Pull Request, exact SHA, attempted connected paths, impossibility evidence, exact targets/provider, canonical UI action, automatic-resumption condition, and connected audit. The live destination is revalidated before persistence and again before publication. Deduplication requires both the exact persisted record and an immutable `github-actions[bot]` comment containing the exact reason/Issue/Pull Request/SHA marker. An untrusted or edited comment cannot suppress a valid notice, and a trusted comment without the matching exact record fails closed.

Routine technical failures, retry exhaustion, no progress, missing evidence, merge state, path denial, untrusted evidence, authentication declarations without proved provider-UI necessity, or unresolved ambiguity cannot use the human-only formatter. Automation resumes automatically when the audited UI condition changes; a new owner message is not required.

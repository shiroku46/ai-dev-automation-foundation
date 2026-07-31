# Operating rules

## Authoritative repositories

This public Foundation repository and its public E2E repository are the implementation sources of truth. Private predecessor repositories are archives only.

## Ordinary flow

1. A trusted Issue defines the goal, acceptance criteria, exact allowed paths, prohibited effects, and validation.
2. The owner starts the Queue with an exact standalone `/claude-run`, or the trusted default-branch supervisor dispatches it.
3. Claude writes a dedicated branch and a Draft Pull Request.
4. Public Pull Request checks execute the exact candidate SHA with `contents: read`, no Secrets, no OIDC, and no write permission.
5. Fixed default-branch trusted checks create GitHub-owned immutable workflow-run and job evidence for the same SHA.
6. Codex independently reviews that exact SHA.
7. The supervisor verifies provenance, scope, protected authorization, complete immutable evidence, review state, mergeability, and the current head.
8. The supervisor marks the Pull Request ready and merges through the Merge API with the exact expected head SHA.

A separate human merge click is not a policy requirement.

## Protected changes

Protected changes include `.github/**`, `bootstrap/**`, supervisor and security-policy code, permission changes, authentication, repository settings, billing, deployment, production, and destructive data operations.

A protected source change requires a trusted Issue containing an exact authorization contract:

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

The supervisor fails closed when the contract is absent or does not cover every protected changed or renamed path.

## Internal stops are durable and non-notifying

Retry exhaustion, no progress, stale or incomplete evidence, blocking review, merge state, ambiguous technical conditions, and ordinary protected-path denial are internal automation states. They must never become routine requests for a person to merge, approve, retry, close, resolve a review, change permissions or settings, alter billing, or deploy.

Before persisting an internal stop, the runtime performs the mandatory self-resolution audit against the live exact SHA. The audit rechecks repository metadata; current Pull Request head and mergeability; complete changed and renamed paths; source Issue trust and protected authorization; fixed workflow metadata; immutable workflow-run and job evidence; GitHub check evidence; Codex evidence and unresolved threads; available collaborator permission; idempotency; and alternative connected recovery paths. It fetches the live Pull Request again after all queries and immediately before any record or automated close. If any query fails or the head moves, no record is written and no stop effect is applied.

Internal stop records are sanitized canonical JSON files on the fixed non-default branch `automation-internal-stops`. Their deterministic path is:

```text
automation-stops/pr-<number>/<exact-sha>/<REASON_CODE>.json
```

The record contains `notification: false`, no required human action, the reason, Issue, Pull Request, exact SHA, bounded detail, and the connected audit evidence. The deterministic path is the idempotency key: an existing matching record creates no duplicate commit. Internal stops are never posted as Issue or Pull Request comments. The supervisor may retain `ai-blocked`, and a deliberately disposable negative E2E Pull Request may be closed after the record is safely persisted.

Codex no-progress is measured from the immutable trusted `github-actions[bot]` exact-SHA request comment, not from Pull Request-wide `updated_at`. Indeterminate mergeability no-progress is measured from the latest immutable successful trusted-check or clean exact-SHA Codex evidence timestamp, not from unrelated Pull Request activity.

## Human-only notice boundary

A separate fail-closed path may notify a person only for one of these reason codes:

- `HUMAN_ONLY_ACCOUNT_LEVEL_REPOSITORY_CREATION_UI_UNAVAILABLE`
- `HUMAN_ONLY_CREDENTIAL_PROVIDER_UI_REQUIRED`
- `HUMAN_ONLY_DISCONNECTED_INTEGRATION_RECONNECTION_UI_REQUIRED`

A human-only notice requires an exact trusted source Issue, live open same-repository Pull Request, lowercase 40-character current head SHA, concrete connected paths already attempted, concrete impossibility evidence, exact affected targets or provider, one canonical reason-compatible provider UI action, an automatic-resumption condition, and the same mandatory connected self-resolution audit. The live Pull Request, exact head, source Issue linkage, Issue author, and candidate provenance are revalidated immediately before publication.

Deduplication trusts only an immutable `github-actions[bot]` comment containing the exact reason/Issue/Pull Request/SHA marker. An untrusted contributor comment cannot suppress a valid notice. Routine technical failures, retry exhaustion, no progress, missing evidence, merge state, protected-path denial, untrusted evidence, authentication declarations without proved provider-UI necessity, or unresolved ambiguity cannot use the human-only formatter.

Automation resumes automatically when the audited UI condition changes; a new owner message is not required.

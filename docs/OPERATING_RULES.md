# Operating rules

## Authoritative repositories

This public Foundation repository and its public E2E repository are the implementation sources of truth. Private predecessor repositories are archives only.

## Ordinary flow

1. A trusted Issue defines goal, acceptance criteria, allowed paths, prohibited effects, and validation.
2. The owner starts the queue with an exact standalone `/claude-run`, or the trusted supervisor dispatches the queue from default-branch code.
3. Claude writes a dedicated branch.
4. A Draft Pull Request is created.
5. Read-only checks run on the exact current head SHA.
6. Codex independently reviews that exact SHA.
7. The supervisor verifies scope, provenance, authorization, complete checks, review evidence, mergeability, and the current head.
8. The supervisor marks the Pull Request ready and merges with an expected-head-SHA guard.

A separate human merge click is not a policy requirement.

## Protected changes

Protected changes include `.github/**`, `bootstrap/**`, supervisor and security-policy code, permission changes, authentication, repository settings, billing, deployment, production, and destructive data operations.

Ordinary protected source changes require a maintainer-authored Issue containing:

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

The supervisor fails closed when this contract is absent or does not cover every protected path.

## Internal stops are non-notifying

A retry limit, no-progress state, stale or incomplete evidence, mergeability state, review finding, ambiguous technical condition, or unauthorized protected path is an internal automation state. The supervisor records one reason-and-SHA-bound `stop_report`, includes its self-resolution audit, and sets `notification: false` and `required_human_action: none`.

An internal stop must never ask a person to merge, approve, retry, close, resolve review, change workflow permissions or settings, alter billing, or deploy. Repeated reconciliation of the same reason and exact SHA must not create duplicate comments.

Before recording an internal stop, the runtime rechecks repository metadata, fixed workflow-run and job evidence, changed and renamed paths, scope and authorization, Codex/review provenance, available permissions, idempotency, and alternative connected repair paths.

## Human-only notice boundary

A separate fail-closed formatter may notify a person only for one of these reason codes:

- `HUMAN_ONLY_ACCOUNT_LEVEL_REPOSITORY_CREATION_UI_UNAVAILABLE`
- `HUMAN_ONLY_CREDENTIAL_PROVIDER_UI_REQUIRED`
- `HUMAN_ONLY_DISCONNECTED_INTEGRATION_RECONNECTION_UI_REQUIRED`

Every human-only notice must contain an exact Issue, Pull Request, lowercase 40-character head SHA, concrete connected paths already attempted, impossibility evidence, the exact affected target or provider, one canonical reason-compatible provider UI action, and an automatic-resumption condition. The notice is deduplicated by reason, Issue, Pull Request, and exact SHA.

Routine technical failures, retry exhaustion, no progress, missing checks, merge state, protected-path denial, untrusted evidence, authentication declarations without proved provider-UI necessity, or unresolved ambiguity cannot use the human-only formatter.

Automation resumes automatically when the audited UI condition changes; a new owner message is not required.

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

## Self-resolution and stop boundary

Before recording a stop, automation must audit repository and Pull Request metadata, default-branch workflow identity, exact-SHA run/job evidence, changed and renamed paths, trusted Issue authorization, Codex evidence, review threads, available GitHub permissions, idempotency markers, and every bounded connected repair path.

Routine technical failure is not a human-action request. Retry exhaustion, no progress, merge state, untrusted or stale evidence, ambiguity, ordinary permission/workflow/authentication declarations, protected-path denial, and unavailable bounded repair are recorded as one deduplicated **non-notifying internal stop**. The record must state `human_action_required: false` and must never ask a person to merge, approve, retry, close, resolve a review, change permissions or repository settings, increase a budget, or deploy.

`ESCALATE_HUMAN` is fail-closed and limited to exactly three reason families after an exact-SHA/reason-bound self-resolution audit proves no callable connected path exists:

- `HUMAN_ONLY_ACCOUNT_LEVEL_REPOSITORY_CREATION_UI_UNAVAILABLE` — account-level repository creation or GitHub App connection/reconnection UI;
- `HUMAN_ONLY_CREDENTIAL_PROVIDER_UI_REQUIRED` — genuine credential acquisition or renewal, MFA, CAPTCHA, hardware key, trusted device, or provider identity-verification UI;
- `HUMAN_ONLY_DISCONNECTED_INTEGRATION_RECONNECTION_UI_REQUIRED` — a disconnected integration for which no callable reconnect action exists.

A valid human-only notice contains the exact Issue, Pull Request, nonempty 40-character head SHA, attempted connected paths, concrete impossibility evidence, one canonical reason-compatible provider-UI action, and the condition under which automation resumes automatically. No substantive decision, routine technical repair, merge click, approval, retry, close, or settings change qualifies.

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

## Stop boundary

Automation may escalate only after a self-resolution audit proves the missing action cannot be performed safely by existing GitHub permissions or default-branch workflows. Human action is limited to genuine account/provider UI, credential, MFA, CAPTCHA, or hardware-key requirements, or an unresolved substantive decision.

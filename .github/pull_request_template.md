## Source Issue

Closes #

## Risk tier

- [ ] low — documentation, formatting, generated metadata, or tests-only with no protected behavior
- [ ] standard — ordinary product code with no protected category
- [ ] protected — workflow, permission, authentication/Secret interface, supervisor/security policy, settings, billing, deployment/production, or destructive behavior

## Exact remote candidate

- branch:
- GitHub-visible exact head SHA:
- expected base branch:

## Exact changed and renamed paths

- 

## Required exact-head checks

- [ ] Foundation CI / repository validation
- [ ] Foundation or target unit tests
- [ ] configured product lint/test/type-check/build checks

## Review evidence

- low: no external Codex requirement after scope and checks pass
- standard: clean exact-SHA Codex or `foundation-coordinator-review:<sha>:clean` from a trusted unedited comment with a nonempty summary
- protected: clean exact-SHA Codex through an owner/connector-supported request

- [ ] applicable exact-SHA review tier satisfied
- [ ] no unresolved review thread

## Minimum safety

- [ ] no automation direct push to the default branch
- [ ] exact remote SHA, same-repository source, and trusted task scope verified
- [ ] proposed-branch code did not execute with Secrets, OIDC, or repository write permission
- [ ] no Secret value was read, printed, copied, persisted, or exposed
- [ ] deployment/production/settings/billing/destructive effects are absent or separately authorized
- [ ] final live PR/head/hold/scope/check/review/mergeability recheck remains required
- [ ] merge will use the exact expected head SHA

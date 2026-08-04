# Operating rules

## Authoritative repositories and policy

The public Foundation repository and its public E2E repository are the implementation and acceptance sources of truth. Private predecessor repositories are archives only.

`docs/MINIMUM_SAFETY_PROFILE.md` is the authoritative day-to-day development policy. When older text assumes that Claude always implements or Codex always reviews, the GitHub-centered single-auditor policy takes precedence.

## Mandatory Phase 0 before ordinary flow

Every newly bootstrapped repository must complete setup steps 1–5 in `docs/PROJECT_STARTUP.md` before the harmless Bootstrap acceptance exercise. Successful acceptance is the final Phase 0 gate. Product Issues and implementation start only after that gate passes.

The coordinator performs all connected inspections first. When no repository-settings API is callable, the owner must complete this one-time GitHub UI action in the exact target repository before acceptance:

`Settings` → `Actions` → `General` → `Workflow permissions`

1. select **Read and write permissions**;
2. enable **Allow GitHub Actions to create and approve pull requests**;
3. save the setting.

Pre-PR Phase 0 guidance is a narrow startup exception, not a runtime GitHub notice. It is delivered directly in the initiating project conversation before GitHub orchestration starts, contains only non-secret navigation or a local command plus the automatic-resumption condition, does not call `human_only_notice()`, does not publish a GitHub comment, does not require an Issue/PR destination, and does not create or relax a runtime reason code. Completion is later recorded in the non-secret Bootstrap acceptance evidence.

The setup prerequisites also include exact-repository GitHub/Codex access, an exact-repository Codex environment, the configured credential name when OAuth is used, and enabled Actions and Foundation workflows. After those prerequisites pass, the harmless acceptance candidate proves that branch, Pull Request, check, audit, and guarded merge orchestration work and thereby completes Phase 0.

Do not retry a stalled write-capable workflow or ask the owner to repost commands until the Workflow-permissions setting has been checked. After acceptance, do not request the setup again unless connected evidence shows that it was reset or the integration is no longer usable.

## Ordinary flow

1. Phase 0 acceptance for the exact repository is already complete and recorded without Secret values.
2. A trusted owner-authored Issue states the goal, risk tier, acceptance criteria, every allowed changed or renamed path, prohibited effects, and validation.
3. The coordinating ChatGPT uses the connected GitHub App/API to create one dedicated same-repository branch from the intended base.
4. The coordinator implements through GitHub-visible file and commit operations, changes only authorized paths, and opens or updates one Pull Request.
5. Public Pull Request checks execute the exact candidate SHA with `contents: read`, no Secrets, no OIDC, and no write permission.
6. Fixed default-branch trusted checks and configured product checks create exact-head evidence for that same GitHub-visible SHA.
7. The coordinator inspects the exact diff, changed and renamed paths, required checks, and current remote head.
8. Apply the risk-tier audit rule:
   - low risk: coordinator diff review and exact-head checks; external audit is optional;
   - standard risk: one clean exact-SHA audit from Codex **or** Claude;
   - protected risk: explicit protected authorization and one clean exact-SHA audit from Codex **or** Claude.
9. Review findings are corrected through the same GitHub-centered branch. Any head change invalidates stale check and audit evidence.
10. Immediately before merge, revalidate provenance, scope, protected authorization, complete required checks, selected-auditor evidence, unresolved threads, hold state, mergeability, and the current head.
11. Merge through the Merge API with the exact expected head SHA.

A separate human merge click is not required. Claude and Codex are alternative audit providers, not mandatory sequential implementation and review stages.

No `workflow_dispatch` or `repository_dispatch` event payload may authorize a candidate, source Issue, changed path, workflow, ref, repository, command, auditor, or merge.

## Implementation route

GitHub direct implementation is the default. The connected GitHub route owns branch creation, authorized file writes, commits, Pull Request publication, correction publication, and remote-SHA confirmation.

Codex or Claude implementation is an exceptional bounded fallback only when GitHub-direct implementation cannot reasonably perform the authorized change or the owner explicitly requests that provider. A provider-reported local commit is incomplete until the expected GitHub-visible remote branch head equals the reported commit.

## Single-provider audit and quota control

- Select at most one routine external auditor for an exact head: `codex` or `claude`.
- Prefer the provider with usable capacity and a working repository connection.
- Quota, setup, connection, or generic error responses are `route-unavailable`, not audit evidence.
- If the selected provider becomes unavailable before valid completion, switch once to the other provider.
- After one clean exact-SHA audit, do not request the second provider routinely.
- A changed head requires one fresh audit from one selected provider.
- A second provider is allowed only after an unresolved first-auditor blocker, explicit owner request, or a documented exceptional-risk rule.
- Do not post repeated identical provider requests.

Provider quota exhaustion never delegates routine implementation, retry, approval, or merge work to the owner. Low-risk work continues through checks and coordinator review. Standard or protected work that still requires an external audit remains in a non-notifying blocked state until one provider route is usable.

## Issue scope and protected authorization

Every changed and renamed path must match the bounded scope declared by a trusted owner-authored Issue. Only exact repository-relative paths and bounded suffix patterns such as `tests/**` are accepted.

Protected changes include `.github/**`, `bootstrap/**`, supervisor and security-policy code, permission changes, authentication, repository settings, billing, deployment, production, and destructive data operations. Protected work requires a nonempty category, exact authorized paths, operation, prohibited effects, validation, and rollback contract.

```text
## Allowed paths
- .github/workflows/example.yml

<!-- foundation-protected-authorization
category: workflow
paths:
- .github/workflows/example.yml
operation: add one reviewed workflow
prohibited: no secrets, deployment, or repository settings
validation: public CI, tests, one exact-SHA Codex-or-Claude audit
rollback: revert the merge commit
-->
```

The supervisor fails closed when any current or previous renamed path exceeds the trusted scope or protected work lacks the stricter contract.

## Native exact-head evidence

Before readiness or merge, the supervisor resolves fixed active default-branch workflow identities for:

- `.github/workflows/ci.yml` / `CI`;
- `.github/workflows/unit-tests.yml` / `Unit Tests`;
- `.github/workflows/e2e.yml` / `E2E Acceptance`, when that fixed workflow exists;
- configured product lint, test, build, and type-check workflows.

Every required candidate workflow file blob is compared with the corresponding stable default-branch blob. The supervisor then requires successful completed runs belonging to the exact Pull Request, same repository, fixed workflow identity, and current head SHA. Missing, pending, cancelled, failed, stale-SHA, cross-Pull-Request, wrong-workflow, wrong-repository, candidate-modified-workflow, or candidate-authored evidence cannot authorize progress.

When GitHub records automation-authored Pull Request runs as `action_required` before any job starts, connected automation may create one metadata-only commit on the same authorized branch. The new exact head invalidates all prior evidence and must receive fresh checks and the risk-tier-required audit; no person is asked to approve the run.

## Audit evidence

A valid external audit must identify the exact current 40-character GitHub-visible SHA, be authored through the selected supported Codex or Claude route, contain a substantive result, and report no unresolved blocking finding. Provider setup, quota, connection, account, or generic assistant responses do not count.

The request for the selected provider is preserved as an immutable trusted exact-SHA request comment or equivalent immutable provider-request event. It must identify the selected auditor and exact current SHA. A request for one provider does not authorize or require a routine second-provider request.

A neutral status or request marker authored by `github-actions[bot]` may preserve exact-SHA state, but it does not select the provider, prove that the provider was reached, or count as completed audit evidence. The coordinating owner or connected coordinator uses the supported selected-provider route.

The Pull Request body or machine-readable status records implementation route, exact SHA, risk tier, selected auditor, audit state, required checks, observed conclusions, and next automatic action. Stale-SHA, untrusted-author, edited-without-provenance, content-free, or second-provider routine evidence is ignored.

## Internal stops are durable and non-notifying

Retry exhaustion, provider quota, no progress, stale or incomplete evidence, blocking review, merge state, ambiguous technical conditions, all-path denial, and protected-path denial are internal automation states. They must never become routine requests for a person to implement, merge, approve, retry, close, resolve review state, change permissions or settings, alter billing, or deploy.

Before persisting an internal stop, the runtime performs the mandatory self-resolution audit against the live exact SHA. It rechecks repository metadata; current Pull Request head and mergeability; complete changed and renamed paths; source Issue trust and scope; protected authorization; fixed workflow identities; immutable trusted and native evidence; selected-auditor evidence and unresolved threads; collaborator permission; idempotency; and alternative connected recovery paths. It fetches the live Pull Request again immediately before any record or disposable close. A failed query or moved head produces no effect.

Internal stop records are sanitized canonical JSON on the fixed non-default branch `automation-internal-stops` at:

```text
automation-stops/pr-<number>/<exact-sha>/<REASON_CODE>.json
```

The record contains `notification: false`, `human_action_required: false`, the reason, Issue, Pull Request, exact SHA, bounded detail, selected audit route, and connected evidence. Routine internal stops are never posted as Issue or Pull Request comments and never create or edit routine stop labels.

Audit no-progress is measured from the immutable trusted exact-SHA request comment and immutable result evidence for the selected provider. Native-check and mergeability no-progress use relevant immutable exact-SHA evidence, never Pull Request-wide `updated_at`.

## Human-only notice boundary

Only these reason codes may notify a person:

- `HUMAN_ONLY_ACCOUNT_LEVEL_REPOSITORY_CREATION_UI_UNAVAILABLE`
- `HUMAN_ONLY_CREDENTIAL_PROVIDER_UI_REQUIRED`
- `HUMAN_ONLY_DISCONNECTED_INTEGRATION_RECONNECTION_UI_REQUIRED`

A notice requires a trusted source Issue, live open same-repository Pull Request, lowercase 40-character current head SHA, concrete connected paths already attempted, independently observed impossibility evidence, exact targets or provider, one canonical reason-compatible provider UI action, an automatic-resumption condition, and the same mandatory connected self-resolution audit.

Provider quota exhaustion by itself is never a human-only reason. For account-level repository creation, the runtime independently queries the exact target repositories through the connected GitHub API. Credential and integration-reconnection reasons fail closed until a reason-specific connected provider evidence adapter proves the UI-only condition; generic caller assertions are never sufficient.

Before publication, the runtime persists a sanitized deterministic notice record at:

```text
automation-stops/pr-<number>/<exact-sha>/<HUMAN_ONLY_REASON>.notice.json
```

The live destination is revalidated before persistence and before publication. Routine technical failures, provider limits, missing evidence, merge state, path denial, untrusted evidence, unsupported provider assertions, or unresolved ambiguity cannot use the human-only formatter. Automation resumes automatically when the audited UI condition changes; a new owner message is not required.

# Agent operating contract

1. Read `docs/MINIMUM_SAFETY_PROFILE.md`, `SECURITY.md`, and `docs/OPERATING_RULES.md` before acting.
2. Work from one trusted owner-authored Issue with a risk tier, bounded paths, acceptance criteria, prohibited effects, and required checks.
3. Use the connected GitHub App/API as the default implementation route: create one dedicated same-repository branch, make GitHub-visible commits, and open or update one Pull Request.
4. Never push automation changes directly to `main` and never force-update a shared branch.
5. Treat local or provider-reported commits as advisory until the expected GitHub-visible remote branch head matches the reported SHA.
6. Require every changed and renamed path to match the Issue scope. Accept only exact repository-relative paths and bounded suffix patterns such as `tests/**`.
7. Require explicit protected authorization for workflows, permissions, authentication or Secret interfaces, supervisor/security policy, repository settings, billing, deployment/production, and destructive operations.
8. Keep contributor checks read-only with no Secrets, OIDC, or write permission. Never execute proposed-branch code in a job carrying Secrets, OIDC, or repository write permission.
9. Bind implementation completion, validation, audit, recovery, readiness, and merge to the exact current GitHub-visible head SHA.
10. Require complete successful Foundation and configured product checks for the exact Pull Request head. Treat stale, incomplete, missing, pending, failed, wrong-workflow, wrong-repository, cross-Pull-Request, and candidate-modified-workflow evidence as absent.
11. Apply the risk-tier audit rule: low risk may complete with coordinator diff review and checks; standard and protected risk require one clean exact-SHA audit from Codex **or** Claude.
12. Select only one routine external auditor per exact head. Do not request both Codex and Claude after one valid clean audit.
13. Treat provider quota, setup, connection, or generic error output as `route-unavailable`, not audit evidence. Switch once to the other provider only before valid audit completion.
14. Any head change invalidates previous check and audit evidence. Request one fresh audit from one selected provider when the tier requires it.
15. Use Codex or Claude for implementation only as an explicit or bounded fallback when GitHub-direct implementation cannot reasonably perform the authorized change.
16. Do not ask a person merely to implement, press Merge, Approve, Retry, Close, resolve routine review state, or change routine workflow state.
17. Before any stop or merge, query repository metadata, the live Pull Request, every changed and renamed path, source Issue scope and protected authorization, fixed workflow identities, native and product checks, selected-auditor evidence and threads, permissions, idempotency, and alternative connected paths.
18. Persist routine stops only as deterministic sanitized records with `notification: false` and `human_action_required: false`; provider quota exhaustion is a routine internal state.
19. Human notification is allowed only for the canonical account/provider UI reason codes after connected evidence proves no callable route exists. Quota exhaustion alone is never a human-only reason.
20. `ai-no-merge` always stops merge execution. Immediately before merge, re-fetch the live PR, head, scope, checks, audit evidence, hold state, and mergeability, then use expected-head-SHA protection.

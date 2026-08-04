# Agent operating contract

1. Before the harmless Bootstrap acceptance exercise in a newly bootstrapped repository, read `docs/PROJECT_STARTUP.md` and complete setup steps 1–5 for the exact repository. Successful acceptance is the final Phase 0 gate; product Issues and implementation start only after it passes.
2. Verify every setup item available through connected tools first. When repository-settings APIs are unavailable, instruct the owner once to open `Settings` → `Actions` → `General` → `Workflow permissions`, select **Read and write permissions**, enable **Allow GitHub Actions to create and approve pull requests**, and save.
3. Do not start Bootstrap acceptance until GitHub/Codex access, the exact-repository Codex environment, required credential name, Actions/workflows, and both Workflow-permissions settings are confirmed. Do not request the setup again after acceptance unless connected evidence shows it is no longer usable.
4. Read `docs/MINIMUM_SAFETY_PROFILE.md`, `SECURITY.md`, `docs/PROJECT_STARTUP.md`, and `docs/OPERATING_RULES.md` before acting.
5. Work from one trusted owner-authored Issue with a risk tier, bounded paths, acceptance criteria, prohibited effects, and required checks.
6. Use the connected GitHub App/API as the default implementation route: create one dedicated same-repository branch, make GitHub-visible commits, and open or update one Pull Request.
7. Never push automation changes directly to `main` and never force-update a shared branch.
8. Treat local or provider-reported commits as advisory until the expected GitHub-visible remote branch head matches the reported SHA.
9. Require every changed and renamed path to match the Issue scope. Accept only exact repository-relative paths and bounded suffix patterns such as `tests/**`.
10. Require explicit protected authorization for workflows, permissions, authentication or Secret interfaces, supervisor/security policy, repository settings, billing, deployment/production, and destructive operations.
11. Keep contributor checks read-only with no Secrets, OIDC, or write permission. Never execute proposed-branch code in a job carrying Secrets, OIDC, or repository write permission.
12. Bind implementation completion, validation, audit, recovery, readiness, and merge to the exact current GitHub-visible head SHA.
13. Require complete successful Foundation and configured product checks for the exact Pull Request head. Treat stale, incomplete, missing, pending, failed, wrong-workflow, wrong-repository, cross-Pull-Request, and candidate-modified-workflow evidence as absent.
14. Apply the risk-tier audit rule: low risk may complete with coordinator diff review and checks; standard and protected risk require one clean exact-SHA audit from Codex **or** Claude.
15. Select only one routine external auditor per exact head. Do not request both Codex and Claude after one valid clean audit.
16. Treat provider quota, setup, connection, or generic error output as `route-unavailable`, not audit evidence. Switch once to the other provider only before valid audit completion.
17. Any head change invalidates previous check and audit evidence. Request one fresh audit from one selected provider when the tier requires it.
18. Use Codex or Claude for implementation only as an explicit or bounded fallback when GitHub-direct implementation cannot reasonably perform the authorized change.
19. Do not ask a person merely to implement, press Merge, Approve, Retry, Close, resolve routine review state, or change routine workflow state.
20. Before any stop or merge, query repository metadata, the live Pull Request, every changed and renamed path, source Issue scope and protected authorization, fixed workflow identities, native and product checks, selected-auditor evidence and threads, permissions, idempotency, and alternative connected paths.
21. Persist routine stops only as deterministic sanitized records on the fixed non-default branch `automation-internal-stops`, with `notification: false` and `human_action_required: false`; provider quota exhaustion is a routine internal state.
22. Human notification is allowed only for the canonical account/provider UI reason codes after connected evidence proves no callable route exists. Quota exhaustion alone is never a human-only reason. Pre-PR Phase 0 Workflow-permissions guidance is delivered directly in the initiating project conversation, contains only non-secret navigation and the resume condition, does not call `human_only_notice()`, does not publish to GitHub, and does not create a new runtime reason code.
23. `ai-no-merge` always stops merge execution. Immediately before merge, re-fetch the live PR, head, scope, checks, audit evidence, hold state, and mergeability, then use expected-head-SHA protection.

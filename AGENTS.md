# Agent operating contract

1. Before the harmless Bootstrap acceptance exercise in a newly bootstrapped repository, read `docs/PROJECT_STARTUP.md` and complete its setup steps 1–5 for the exact repository. Successful acceptance is the final Phase 0 gate; product Issues and implementation start only after it passes.
2. Verify every setup item available through connected tools first. When repository-settings APIs are unavailable, instruct the owner once to open `Settings` → `Actions` → `General` → `Workflow permissions`, select **Read and write permissions**, enable **Allow GitHub Actions to create and approve pull requests**, and save.
3. Do not start Bootstrap acceptance until GitHub/Codex access, the exact-repository Codex environment, required credential name, Actions/workflows, and both Workflow-permissions settings are confirmed. Do not start product work until the harmless acceptance evidence is also confirmed. Do not request the setup again after acceptance unless connected evidence shows it is no longer usable.
4. Work from one trusted owner-authored Issue.
5. Use a dedicated branch and Draft Pull Request; never push directly to `main`.
6. Require every changed and renamed path to match the Issue's ordinary allowlist. Accept only exact repository-relative paths and bounded suffix patterns such as `tests/**`.
7. Require each protected path to appear independently in both the ordinary allowlist and the protected-change authorization block.
8. Do not access another repository, deploy, alter Secrets, change billing, mutate production, or expand permissions.
9. Bind validation, review, recovery, readiness, and merge to the current exact head SHA.
10. Treat stale, candidate-authored, incomplete, missing, pending, failed, wrong-workflow, wrong-repository, cross-Pull-Request, and candidate-modified-workflow evidence as absent.
11. Before readiness or merge, compare each required candidate workflow file blob with the stable default-branch blob, then require a successful same-repository native Pull Request run for the fixed `CI`, `Unit Tests`, and fixed `E2E Acceptance` identity when present.
12. Keep contributor checks read-only with no Secrets, OIDC, or write permission. Keep write-capable execution default-branch-controlled and never execute proposed-branch code there.
13. Do not ask a person merely to press Merge, Approve, Retry, Close, resolve routine review state, or change routine workflow state.
14. Before any stop, query repository metadata, the live Pull Request twice, every changed and renamed path, source Issue allowlist and protected authorization, fixed workflow identities and stable blobs, immutable trusted run/job and native evidence, Codex and threads, permissions, idempotency, and alternative connected paths.
15. Persist routine stops only as deterministic sanitized JSON on `automation-internal-stops`; never post a routine stop comment or mutate a routine stop label. A failed audit or moved head writes nothing.
16. Order combined Codex comments and reviews by immutable event time. Measure no-progress only from immutable exact-SHA request, workflow, and review evidence.
17. Human notification is allowed only for the three canonical account/provider UI reason codes after the same connected audit and live destination revalidation. Pre-PR Phase 0 Workflow-permissions guidance is delivered directly in the initiating project conversation, contains only non-secret navigation and the resume condition, does not call `human_only_notice()`, does not publish to GitHub, and does not create a new runtime reason code.
18. Account-level repository absence must be independently derived from connected GitHub API queries of the exact targets, and caller assertions must match. Credential and reconnection reasons fail closed without a reason-specific connected provider adapter.
19. Persist the exact deterministic human-only audit record before publication. Deduplication requires both that record and an immutable `github-actions[bot]` notice comment; untrusted or edited comments do not count.
20. `ai-no-merge` always stops merge execution, and final merge uses expected-head-SHA protection.

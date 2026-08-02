# Agent operating contract

1. Before the first product Issue or implementation request in a newly bootstrapped repository, read and complete `docs/PROJECT_STARTUP.md` for the exact repository.
2. Verify every setup item available through connected tools first. When repository-settings APIs are unavailable, instruct the owner once to open `Settings` → `Actions` → `General` → `Workflow permissions`, select **Read and write permissions**, enable **Allow GitHub Actions to create and approve pull requests**, and save.
3. Do not start Bootstrap acceptance or product work until GitHub/Codex access, the exact-repository Codex environment, required credential name, Actions/workflows, both Workflow-permissions settings, and harmless acceptance evidence are confirmed. Do not request the setup again after acceptance unless connected evidence shows it is no longer usable.
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
17. Human notification is allowed only for the canonical account/provider UI reason codes after the same connected audit and live destination revalidation. Mandatory Phase 0 Workflow-permissions setup is one such UI-only prerequisite when no connected settings endpoint exists.
18. Account-level repository absence must be independently derived from connected GitHub API queries of the exact targets, and caller assertions must match. Credential and reconnection reasons fail closed without a reason-specific connected provider adapter.
19. Persist the exact deterministic human-only audit record before publication. Deduplication requires both that record and an immutable `github-actions[bot]` notice comment; untrusted or edited comments do not count.
20. `ai-no-merge` always stops merge execution, and final merge uses expected-head-SHA protection.

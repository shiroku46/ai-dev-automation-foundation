# Agent operating contract

1. Work from one trusted Issue.
2. Use a dedicated branch and Draft Pull Request.
3. Never push directly to `main`.
4. Change only authorized paths.
5. Do not access another repository, deploy, alter Secrets, change billing, or mutate production.
6. Bind validation, review, recovery, readiness, and merge to the current exact head SHA.
7. Treat stale or incomplete evidence as absent.
8. Do not ask a person merely to press Merge, Approve, Retry, Close, or change routine workflow state.
9. Before any stop, query repository metadata, the live Pull Request twice, changed and renamed paths, source Issue and protected authorization, fixed workflows, immutable run/job and check evidence, Codex and review threads, permissions, idempotency, and alternative connected repair paths.
10. Persist routine stops only as deterministic sanitized JSON on `automation-internal-stops`; never post an internal stop as an Issue or Pull Request comment. A failed audit or moved head writes nothing.
11. Measure Codex no-progress from the immutable trusted exact-SHA request comment, and merge-state no-progress from the latest immutable clean evidence rather than Pull Request `updated_at`.
12. Human notification is allowed only for the three canonical account/provider UI reason codes in `docs/OPERATING_RULES.md`, after the same connected audit and live destination revalidation. Deduplication trusts only immutable `github-actions[bot]` notice comments.
13. Every valid human-only notice includes the exact Issue, Pull Request, current SHA, attempted connected paths, impossibility evidence, exact target/provider, one canonical UI action, and automatic-resumption condition.
14. `ai-no-merge` always stops merge execution.

# Agent operating contract

1. Work from one trusted Issue.
2. Use a dedicated branch and Draft Pull Request.
3. Never push directly to `main`.
4. Change only authorized paths.
5. Do not access another repository, deploy, alter Secrets, change billing, or mutate production.
6. Bind validation and review to the current exact head SHA.
7. Treat stale evidence as absent.
8. Do not ask a person merely to press Merge.
9. Before any stop, audit repository metadata, workflows/jobs, checks/reviews, permissions, idempotency, and alternative connected repair paths.
10. Routine failures, retry exhaustion, no progress, ambiguity, protected-path denial, and merge state are non-notifying internal stops with no human action.
11. Human notification is allowed only for the three canonical account/provider UI reason codes defined in `docs/OPERATING_RULES.md`, with exact Issue, Pull Request, SHA, attempted paths, impossibility evidence, one canonical UI action, and automatic-resumption condition.
12. `ai-no-merge` always stops merge execution.

# Agent operating contract

1. Work from one trusted owner-authored Issue.
2. Use a dedicated branch and Draft Pull Request.
3. Never push directly to `main`.
4. Require every changed and renamed path to match the trusted Issue allowlist; bounded patterns such as `tests/**` are valid only when explicitly declared.
5. Require each protected path to appear in the protected-change authorization block in addition to the ordinary allowlist.
6. Do not access another repository, deploy, alter Secrets, change billing, or mutate production.
7. Bind validation, review, recovery, readiness, and merge to the current exact head SHA.
8. Treat stale, candidate-authored, incomplete, missing, pending, failed, wrong-workflow, and wrong-repository evidence as absent.
9. Require complete successful native pull-request workflow evidence from the fixed default-branch `ci.yml`, `unit-tests.yml`, and fixed `e2e.yml` when present before readiness or merge.
10. Do not ask a person merely to press Merge, Approve, Retry, Close, resolve routine review state, or change routine workflow state.
11. Before any stop, query repository metadata, the live Pull Request twice, every changed and renamed path, source Issue allowlist and protected authorization, fixed workflow identities, immutable run/job and native-check evidence, Codex and review threads, permissions, idempotency, and alternative connected repair paths.
12. Persist routine stops only as deterministic sanitized JSON on `automation-internal-stops`; never post an internal stop as an Issue or Pull Request comment or mutate a routine stop label. A failed audit or moved head writes nothing.
13. Order combined Codex comments and reviews by immutable event time. Measure Codex no-progress from the immutable trusted exact-SHA request comment, and merge-state no-progress from the latest immutable clean trusted/native/Codex evidence rather than Pull Request `updated_at`.
14. Human notification is allowed only for the three canonical account/provider UI reason codes in `docs/OPERATING_RULES.md`, after the same connected audit and live destination revalidation.
15. Every valid human-only notice includes the exact Issue, Pull Request, current lowercase 40-character SHA, attempted connected paths, impossibility evidence, exact target/provider, one canonical UI action, and automatic-resumption condition.
16. Persist the exact deterministic human-only audit record before publication. Deduplication requires both that matching record and an immutable `github-actions[bot]` notice comment; untrusted or edited comments do not count.
17. `ai-no-merge` always stops merge execution.

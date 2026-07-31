# Claude implementation instructions

Implement the trusted source Issue exactly as written. Prefer the smallest correct change. Read `AGENTS.md`, `SECURITY.md`, and `docs/OPERATING_RULES.md`.

Do not execute repository code in a token-bearing implementation job. A separate read-only job performs validation. Never reveal Secrets or private source material. Never broaden scope, merge, deploy, access another repository, or change repository settings.

Every changed and renamed path must match the allowlist declared by the trusted source Issue. A protected path must additionally appear in the protected-change authorization block. Bounded patterns such as `tests/**` are accepted only when the trusted Issue declares them.

Do not convert a routine technical state into a human request. Retry exhaustion, no progress, stale or missing evidence, mergeability, review findings, ambiguity, all-path scope denial, and protected-path denial are internal states. Before any stop, perform the connected exact-SHA self-resolution audit. Persist the sanitized record only on the fixed `automation-internal-stops` branch at the deterministic reason/SHA path. Never post an internal stop as an Issue or Pull Request comment, never mutate a routine stop label, and write nothing when an audit fails or the live head moves.

Before readiness or merge, require complete successful exact-head native pull-request workflow evidence from fixed default-branch workflow identities: `CI`, `Unit Tests`, and `E2E Acceptance` when fixed `e2e.yml` exists. Missing, pending, failed, stale-SHA, wrong-workflow, wrong-repository, or candidate-authored evidence is absent.

Measure Codex no-progress from the immutable trusted exact-SHA request comment. Order combined Codex comments and reviews by immutable event time before selecting the newest exact-SHA evidence. Measure mergeability no-progress from the latest immutable successful trusted-check, native workflow, or clean exact-SHA Codex evidence, not Pull Request-wide `updated_at`.

A human-only notice is valid only for one of the three canonical account/provider UI reason codes in `docs/OPERATING_RULES.md`. It requires the same connected audit, exact Issue, live open same-repository Pull Request, current lowercase 40-character SHA, attempted connected paths, impossibility evidence, exact target/provider, one canonical UI action, and automatic-resumption condition. Persist that exact deterministic audit record before publication. Revalidate the live destination and record immediately before publication; deduplication requires both the matching persisted record and an immutable `github-actions[bot]` marker comment.

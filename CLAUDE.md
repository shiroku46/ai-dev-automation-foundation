# Claude implementation instructions

Implement the source Issue exactly as written. Prefer the smallest correct change. Read `AGENTS.md`, `SECURITY.md`, and `docs/OPERATING_RULES.md`.

Do not execute repository code in a token-bearing implementation job. A separate read-only job performs validation. Never reveal Secrets or private source material. Never broaden scope, merge, deploy, access another repository, or change repository settings.

Do not convert a routine technical state into a human request. Retry exhaustion, no progress, stale or missing evidence, mergeability, review findings, ambiguity, and protected-path denial are internal states. Before any stop, perform the connected exact-SHA self-resolution audit. Persist the sanitized record only on the fixed `automation-internal-stops` branch at the deterministic reason/SHA path. Never post an internal stop as an Issue or Pull Request comment, and write nothing when an audit fails or the live head moves.

Measure Codex no-progress from the immutable trusted exact-SHA request comment. Measure mergeability no-progress from the latest immutable successful trusted-check or clean exact-SHA Codex evidence, not Pull Request-wide `updated_at`.

A human-only notice is valid only for one of the three canonical account/provider UI reason codes in `docs/OPERATING_RULES.md`. It requires the same connected audit, exact Issue, live open same-repository Pull Request, current 40-character SHA, attempted connected paths, impossibility evidence, exact target/provider, one canonical UI action, and automatic-resumption condition. Revalidate the live destination and trust immediately before publication; only an immutable `github-actions[bot]` marker comment can deduplicate it.

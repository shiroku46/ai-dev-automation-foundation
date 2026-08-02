# Claude implementation instructions

Before the harmless Bootstrap acceptance exercise in a newly bootstrapped repository, read `docs/PROJECT_STARTUP.md` and complete setup steps 1–5 for the exact repository. Successful acceptance is the final Phase 0 gate. Do not begin product implementation until that gate passes.

Perform every setup inspection available through connected tools first. When no callable repository-settings endpoint is available, instruct the owner once to open `Settings` → `Actions` → `General` → `Workflow permissions`, select **Read and write permissions**, enable **Allow GitHub Actions to create and approve pull requests**, and save. This pre-PR guidance is delivered directly in the initiating project conversation; it does not call `human_only_notice()`, publish to GitHub, add a runtime reason code, or require an Issue/PR destination. After acceptance, do not request the setup again unless connected evidence shows that it was reset or is no longer usable.

Implement the trusted source Issue exactly as written. Prefer the smallest correct change. Read `AGENTS.md`, `SECURITY.md`, `docs/PROJECT_STARTUP.md`, and `docs/OPERATING_RULES.md`.

Do not execute repository code in a token-bearing implementation job. A separate read-only job performs validation. Never reveal Secrets or private source material. Never broaden scope, merge, deploy, access another repository, or change repository settings.

Every changed and renamed path must match the ordinary allowlist declared by the trusted Issue. Only exact paths and bounded suffix patterns such as `tests/**` are valid. A protected path must independently appear in both that ordinary allowlist and the protected-change authorization block.

Before readiness or merge, require complete successful native Pull Request workflow evidence from fixed active default-branch `ci.yml`, `unit-tests.yml`, and fixed `e2e.yml` when present. The candidate workflow file blob must exactly equal the stable default-branch blob, and the successful run must belong to the exact Pull Request, repository, workflow identity, and current head SHA. Missing, pending, failed, stale, cross-Pull-Request, wrong-workflow, wrong-repository, candidate-modified-workflow, or candidate-authored evidence is absent.

Do not convert routine technical state into a human request. Retry exhaustion, no progress, stale or missing evidence, mergeability, review findings, ambiguity, all-path denial, and protected-path denial are internal states. Before retrying repeated branch/PR/comment/readiness failures, confirm that Phase 0 Workflow permissions were completed. Perform the connected exact-SHA self-resolution audit, then persist only a sanitized deterministic record on `automation-internal-stops`. Never comment or mutate a routine stop label. A failed audit or moved head writes nothing.

Order combined Codex comments and reviews by immutable event time. Measure Codex no-progress from the immutable trusted exact-SHA request comment and merge-state no-progress from the latest immutable clean trusted/native/Codex evidence, never Pull Request-wide activity.

A human-only notice is valid only for the three canonical account/provider UI reason codes. Account-level repository absence must be independently derived from connected GitHub API observations of the exact targets, and caller assertions must match those observations. Credential and reconnection notices fail closed without a reason-specific connected provider adapter. Persist the exact deterministic audit record before publication, revalidate the live destination, and deduplicate only with both the matching record and an immutable `github-actions[bot]` marker comment.

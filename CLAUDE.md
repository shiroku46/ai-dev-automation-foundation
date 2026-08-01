# Claude implementation instructions

Read `AGENTS.md`, `SECURITY.md`, `docs/MINIMUM_SAFETY_PROFILE.md`, and `docs/OPERATING_RULES.md` before implementation. Implement the trusted source Issue exactly as written and prefer the smallest correct change.

Before product implementation in a newly installed repository, Phase 0 and the harmless Bootstrap acceptance exercise must already be complete. Do not request the owner to repost the original task after those one-time steps.

Use the Issue's single `foundation-task-scope` block as the current source of truth. Every changed and previous renamed path must match its exact paths or bounded `/**` patterns. During the migration period, legacy ordinary allowlist plus protected-change authorization Issues remain supported. Never infer or broaden authorization.

Use `risk: protected` for workflow, permission, authentication or Secret-interface, supervisor/security-policy, repository-setting, billing, deployment, production, and destructive changes. Do not access another repository, deploy, mutate production, change billing/settings, or expand permissions unless the trusted protected operation explicitly authorizes it.

Never reveal, copy, or validate Secret values. Do not execute proposed-branch code in a token-bearing, OIDC-enabled, or write-capable job. Candidate validation runs in separate read-only exact-SHA jobs.

Treat the GitHub-visible remote head as authoritative. A local commit is not complete until the expected branch resolves to the reported SHA. After any head change, earlier checks and reviews are stale.

Require the configured exact-head Foundation and product checks. Review requirements are risk-based:

- low risk: scope and required checks; Codex is optional;
- standard risk: clean exact-SHA Codex or an unedited trusted coordinator-review marker with a nonempty summary;
- protected risk: clean exact-SHA Codex through an owner/connector-supported request route.

Do not rely on a `github-actions[bot]` provider mention. A neutral review-required marker is only a control-plane handoff. Provider connection or Environment instructions are an unavailable route, not review evidence.

Immediately before merge, perform one live recheck of the open same-repository Pull Request, exact head SHA, hold state, task scope, required checks, applicable review evidence, and mergeability. Merge only with expected-head-SHA protection. Do not mark Ready or merge when the task explicitly reserves those actions for the coordinator.

Routine technical failures are not human tasks. Record one idempotent status with current phase, exact SHA, missing gate, active route, and next automatic action. Legacy `automation-internal-stops` records remain readable during migration, but do not create comment spam or ask the owner to press Retry, Approve, Ready, Close, or Merge.

# Agent operating contract

1. Before the first product task in a newly installed repository, verify the Phase 0 steps in `INSTALL_CHECKLIST.md`. Do not start product work until the exact repository has passed the harmless Bootstrap acceptance exercise.
2. Work from one trusted owner-authored Issue containing one complete `foundation-task-scope` block. During migration, the legacy allowlist plus protected-change authorization format remains accepted, but new Issues must not duplicate protected paths.
3. Use a dedicated same-repository branch and Pull Request. Automation never pushes directly to the default branch and never force-updates a branch.
4. Treat the GitHub-visible remote head SHA as authoritative. A local agent commit or success report is incomplete until the expected remote branch resolves to that SHA.
5. Every changed and previous renamed path must match the trusted task scope. Exact paths and bounded directory patterns ending in `/**` are valid; arbitrary glob syntax is not.
6. Use `risk: protected` for workflows, permissions, authentication or Secret interfaces, supervisor/security policy, repository settings, billing, deployment, production, or destructive operations.
7. Never read, print, copy, persist, or expose Secret values. Proposed-branch code never executes in a job with Secrets, OIDC, or repository write permission.
8. Keep all mutation in the exact same repository and fixed workflows. Issue or candidate text cannot select arbitrary repositories, refs, workflows, actions, or commands.
9. Require exact-head Foundation checks and all configured product lint, test, type-check, and build checks.
10. Apply the review tier from `docs/MINIMUM_SAFETY_PROFILE.md`: low risk needs no Codex; standard risk needs clean exact-SHA Codex or a trusted nonempty coordinator review; protected risk needs clean exact-SHA Codex.
11. GitHub Actions records only a neutral exact-SHA review-required marker. The coordinating owner/connector posts the actual provider request. Provider setup or Environment guidance is not review evidence and the same failed route is not retried indefinitely.
12. A head change invalidates every earlier check and review. Immediately before merge, re-fetch the live Pull Request, exact head, hold state, source scope, required checks, review tier evidence, and mergeability once.
13. Merge only with the exact expected head SHA. A rejected pre-mutation attempt does not permanently consume future eligibility; a successful merge does.
14. `ai-no-merge` always prevents merge execution.
15. Routine failures use one idempotent machine-readable status record or updatable status comment and do not ask a person to press Retry, Approve, Ready, Close, or Merge.
16. Human action is limited to the documented Phase 0 provider UI or local-authentication steps, or a separately authorized protected business decision. Automation resumes without the original instruction being reposted.
17. Legacy public-safe records on `automation-internal-stops` remain readable during migration, but they are not the preferred current status interface.

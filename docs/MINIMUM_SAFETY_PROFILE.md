# Minimum safety profile

This document is the current practical security baseline for the Foundation and every generated target. The goal is reliable unattended development with a small number of controls that directly prevent material damage. Redundant evidence, repeated equivalent checks, and provider routes that do not work in practice must not block ordinary development.

## Core invariants

1. Automation never pushes directly to the default branch. Every change uses a dedicated same-repository branch and Pull Request.
2. GitHub-visible remote state is authoritative. A local agent commit is incomplete until the expected remote branch resolves to the reported 40-character SHA.
3. Checks, review, readiness, and merge are bound to the current remote head SHA. Evidence for an older SHA is stale.
4. Merge uses expected-head protection and fails when the head changes after the final decision.
5. One trusted owner-authored task-scope block authorizes every changed and renamed path.
6. Protected operations require `risk: protected`, an explicit operation, and explicit prohibited effects.
7. Secret values are never read, printed, copied, persisted, or exposed. Candidate code never executes in a job with Secrets, OIDC, or repository write permission.
8. Automation is limited to the exact same repository and fixed workflows. Candidate or Issue text cannot select arbitrary repositories, refs, workflows, actions, or commands.
9. Foundation checks and configured product lint, test, type-check, and build checks must pass on the exact head.
10. Immediately before merge, the coordinator re-fetches the live PR, head SHA, mergeability, hold state, scope, checks, and required review evidence once.
11. Deployment, production, billing, repository settings, authentication changes, and destructive data operations require separate explicit authorization.

## Task scope contract

New Issues use one block as the source of truth:

```text
<!-- foundation-task-scope
risk: low
paths:
- docs/example.md
operation: update the exact documentation stated in the Issue
prohibited: no product code, workflow, permission, Secret, deployment, or unrelated change
checks:
- CI
-->
```

Allowed risk values are `low`, `standard`, and `protected`.

- `paths` contains exact repository-relative paths or bounded directory patterns ending in `/**`.
- Every changed and previous filename for a rename must match the declared paths.
- `operation` and `prohibited` must be nonempty.
- A change to workflows, permissions, authentication or Secret interfaces, Foundation supervisor/security policy, repository settings, billing, deployment, production, or destructive operations must use `risk: protected`.
- The same protected path is not listed again in a second authorization block.
- Legacy dual-scope Issues may be accepted only during a bounded migration period.

## Review tiers

### Low risk

Low risk includes documentation, formatting, generated metadata, and tests-only changes that do not modify executable runtime behavior, workflows, permissions, authentication, or protected policy.

Requirements:

- exact remote head SHA;
- authorized scope;
- all required exact-head checks pass;
- no external Codex review is required for merge.

Provider review availability must not block a low-risk change.

### Standard risk

Standard risk is ordinary product code with no protected category.

Requirements:

- exact remote head SHA;
- authorized scope;
- all required exact-head Foundation and product checks pass;
- one clean exact-SHA review from either Codex or the trusted coordinator.

A coordinator review uses this marker in an unedited comment authored by the repository owner or configured automation owner:

```text
<!-- foundation-coordinator-review:<40-character-sha>:clean -->
<nonempty summary of the inspected diff, checks, and residual risk>
```

A missing summary, edited comment, untrusted author, or different SHA does not count.

### Protected risk

Protected risk includes workflow, permission, authentication or Secret-interface, supervisor/security-policy, repository-setting, billing, deployment, production, and destructive changes.

Requirements:

- all minimum invariants and exact-head checks;
- a clean exact-SHA Codex review;
- the Codex request is posted through a provider-supported owner or connector identity.

A coordinator review may supplement but does not replace Codex for protected changes.

## Review request behavior

GitHub Actions does not post `@codex review` as the active provider request. It may record a neutral exact-SHA `REVIEW_REQUIRED` state.

The coordinating ChatGPT/GitHub connector posts the actual owner-authored Codex request when the risk tier requires it. Provider setup responses such as requests to connect GitHub or create an Environment are classified as an unavailable review route, not review evidence. The same failed route is not retried indefinitely.

After any head change, all previous review evidence is stale and the applicable review tier is evaluated again.

## Final merge decision

The coordinator keeps one stable evidence snapshot while work is progressing. Immediately before merge it performs one final live recheck of:

- open same-repository PR and expected base branch;
- exact current remote head SHA;
- no hold or no-merge state;
- task scope and risk classification;
- required exact-head checks;
- review evidence required by the risk tier;
- mergeability.

The merge call includes the exact expected head SHA. A rejected pre-mutation attempt does not permanently consume future eligibility; eligibility is recomputed from live evidence.

## Status and recovery

Routine failures produce one idempotent, machine-readable status record or updatable status comment containing the current phase, exact SHA, missing gate, active route, and next automatic action. They do not create repeated comments or ask the owner to press Retry, approve, mark Ready, or merge.

Human action is limited to the documented Phase 0 provider UI or local authentication operations, or a separately authorized protected business decision. After that action is complete, automation resumes without requiring the original development instruction to be reposted.

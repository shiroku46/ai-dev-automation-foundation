# Operating rules

## Authoritative sources

The public Foundation repository is the implementation source of truth. The public E2E repository validates release candidates. `docs/MINIMUM_SAFETY_PROFILE.md` is the current security and review policy. Earlier private sandboxes and superseded operational procedures are archives only.

## Phase 0 before product work

For every new target repository, the coordinating ChatGPT first verifies connected evidence and asks the owner only for missing UI/local-authentication steps:

1. connect Codex/ChatGPT to GitHub and authorize the exact repository;
2. create a Codex Environment for the exact repository;
3. run `claude setup-token` on the owner's authenticated local machine;
4. store the value only as repository Secret `CLAUDE_CODE_OAUTH_TOKEN`.

The token value is never pasted into ChatGPT, Notion, an Issue, Pull Request, source, workflow, or log. After the harmless Bootstrap acceptance exercise passes, these steps are not requested again unless connected evidence proves that authorization, Environment, or credential use is missing or unavailable.

## Ordinary flow

1. A trusted owner-authored Issue defines the goal, acceptance criteria, one `foundation-task-scope` block, and required checks.
2. The coordinator starts one bounded implementation task.
3. Implementation writes a dedicated same-repository branch and Pull Request; automation never pushes directly to the default branch.
4. GitHub-visible remote state is authoritative. Local commits are incomplete until the expected branch resolves to the reported SHA.
5. Read-only exact-head Foundation and configured product checks run without Secrets, OIDC, or write permission.
6. The risk tier determines review: low, standard, or protected.
7. The coordinator performs one final live recheck of the Pull Request, head, hold state, source scope, checks, review evidence, and mergeability.
8. An eligible Pull Request merges with the exact expected head SHA. A separate human merge click is not required.

No `workflow_dispatch` or `repository_dispatch` event payload may authorize a candidate, source Issue, changed path, workflow, ref, repository, command, or merge.

## Unified task scope

New Issues use exactly one block:

```text
<!-- foundation-task-scope
risk: standard
paths:
- src/**
- tests/**
operation: implement the bounded product change described by this Issue
prohibited: no workflow, permission, Secret, deployment, production, or unrelated change
checks:
- CI
- product:test
-->
```

Every changed and previous renamed path must match an exact path or a bounded directory pattern ending in `/**`. Other glob forms are rejected. `operation` and `prohibited` are required.

Protected categories include `.github/**`, `bootstrap/**`, supervisor/security-policy code, permissions, authentication or Secret interfaces, repository settings, billing, deployment, production, and destructive operations. Any protected path or operation requires `risk: protected`.

During a bounded migration period, legacy Issues with an ordinary allowlist and a separate `foundation-protected-authorization` block remain accepted. New Issues do not duplicate the same protected path in two places.

## Review tiers

### Low risk

Documentation, formatting, generated metadata, or tests-only changes that do not alter executable runtime, workflows, permissions, authentication, or protected policy require:

- exact GitHub-visible head SHA;
- authorized scope;
- all required exact-head checks.

Codex is optional and provider availability cannot block completion.

### Standard risk

Ordinary product code without a protected category requires all checks plus either:

- clean exact-SHA Codex evidence; or
- an unedited trusted owner/configured-owner comment containing:

```text
<!-- foundation-coordinator-review:<40-character-sha>:clean -->
<nonempty summary of the exact diff, checks, and residual risk>
```

Untrusted authors, edited comments, empty summaries, and different or stale SHAs do not count.

### Protected risk

Workflow, permission, authentication/Secret-interface, supervisor/security-policy, repository-setting, billing, deployment, production, and destructive changes require clean exact-SHA Codex review. The actual request is posted through an owner/connector-supported identity.

`github-actions[bot]` records only a neutral exact-SHA review-required marker. It does not actively invoke `@codex`. Provider responses asking to connect GitHub or create an Environment are classified as an unavailable route, not review evidence. The same failed route is not repeated indefinitely.

Any head change invalidates every earlier check and review.

## Validation and final merge

Candidate code is never executed in a Secret/OIDC/write-capable job. Fixed read-only workflows validate the exact remote head. Required product lint, test, type-check, and build identities are part of the target contract.

The coordinator may retain one stable evidence snapshot while work progresses. Immediately before mutation it performs one live recheck of:

- open same-repository Pull Request and expected base;
- exact current remote head SHA;
- `ai-no-merge` or other hold state;
- trusted Issue and task scope;
- required exact-head checks;
- review evidence required by the risk tier;
- mergeability.

The merge API call includes the expected head SHA. A rejected attempt before mutation does not permanently consume future eligibility; a successful merge does.

## Status, recovery, and human boundary

Routine failure produces one idempotent machine-readable status record or updatable status comment containing the phase, exact SHA, missing gate, active route, and next automatic action. It does not ask a person to press Retry, Approve, Ready, Close, or Merge and does not create repeated comments.

Human action is limited to the Phase 0 provider UI/local-authentication steps or a separately authorized protected business decision. Every notice identifies the exact target, minimal action, what must not be pasted, and the automatic-resumption condition. Automation resumes without the original instruction being reposted.

## Legacy migration notes

Older generated targets may still contain sanitized JSON records on `automation-internal-stops`. Those records were never posted as Issue or Pull Request comments and remain readable until status migration is complete. Older no-progress logic may refer to an immutable trusted exact-SHA request comment authored by `github-actions[bot]`; new review orchestration uses the neutral review-required marker and the coordinating owner/connector route instead.

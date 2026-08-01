# Public workflow security model

The Foundation keeps a small set of controls that directly prevent material damage. Risk-tier review and operational convenience may vary, but the boundaries below are mandatory.

| Context | Code source | Permissions | Secrets |
|---|---|---:|---:|
| Pull Request checks | exact GitHub-visible Pull Request head | `contents: read` | none |
| Queue implementation | fixed default-branch workflow plus bounded same-repository writer | bounded repository write | configured Claude credential only where required |
| Trusted exact-SHA validation | fixed default-branch workflow; candidate runs only in isolated jobs | `contents: read` in candidate jobs | none |
| Reconciliation/control plane | current default branch | fixed, bounded API permissions | none |
| Final merge | current default-branch guard | bounded Pull Request/content mutation | none |

## Mandatory boundaries

- Automation never pushes directly to the default branch and never force-updates a candidate branch.
- GitHub-visible remote SHA is authoritative. Local-only commits and provider success claims are not merge evidence.
- Proposed-branch code never executes in a job with Secrets, OIDC, or repository write permission.
- Every changed and previous renamed path must match one trusted owner-authored task scope.
- Protected paths and operations require `risk: protected` and a nonempty authorized operation/prohibited-effects statement.
- Candidate or Issue text cannot select arbitrary repositories, refs, workflows, actions, or commands.
- Exact-head Foundation checks and configured product lint, test, type-check, and build checks must succeed.
- A final live recheck binds source, scope, checks, applicable review evidence, mergeability, hold state, and expected head immediately before merge.
- Deployment, production, billing, repository-setting, authentication, Secret-interface, and destructive mutations require separate explicit protected authorization.

## Review evidence

Review is risk-based rather than universally provider-blocking:

- low risk: exact-head scope and checks; external review optional;
- standard risk: clean exact-SHA Codex or a trusted unedited nonempty coordinator review marker for the same SHA;
- protected risk: clean exact-SHA Codex requested through an owner/connector-supported path.

A provider setup message, a stale SHA, an edited or untrusted marker, an empty summary, or unresolved review threads cannot authorize progress. GitHub Actions may publish a neutral exact-SHA review-required marker but does not use a bot-authored provider mention as the active review request.

## Merge race protection

The controller keeps one stable evidence snapshot while work is in progress, then re-fetches the live Pull Request and exact head immediately before mutation. Merge uses the expected head SHA. If the head changes, all prior checks and reviews become stale. A rejected pre-mutation attempt may be retried after fresh evidence; a successful merge consumes the gate.

## Legacy compatibility

Existing generated targets may still use immutable trusted workflow-run/job evidence, bounded recovery attempts, and public-safe `automation-internal-stops` records. These remain accepted during migration, but they do not replace the minimum boundaries above and are not required merely to make a low-risk change mergeable.

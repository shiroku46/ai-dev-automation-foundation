# Minimum safety profile

This document is the authoritative default for day-to-day development performed with this Foundation.

## Default roles

- **Coordinating ChatGPT and the connected GitHub App/API** are the ordinary implementation and publication route. They create a dedicated branch, read and update only Issue-authorized paths, create GitHub-visible commits, open or update one Pull Request, inspect the exact diff, and confirm the remote head SHA.
- **GitHub Actions** performs exact-head Foundation and product validation in read-only jobs.
- **Codex and Claude** are alternative independent auditors. They are not both required for one ordinary Pull Request head.
- **Codex or Claude implementation** is exceptional fallback behavior, used only when GitHub-direct implementation cannot reasonably perform the authorized change or the owner explicitly requests a provider implementation route.

## Ordinary GitHub-centered flow

1. Start from one trusted owner-authored Issue containing a bounded path scope, risk tier, acceptance criteria, prohibited effects, and required checks.
2. Create one same-repository branch from the current intended base. Never push automation changes directly to the default branch.
3. Implement through the connected GitHub write path. Every commit must become visible on the expected remote branch before it counts as progress.
4. Open or update one Pull Request linked to the source Issue.
5. Run required Foundation and product checks on the exact GitHub-visible head SHA.
6. The coordinator inspects the exact diff, changed and renamed paths, check results, and current head.
7. Apply the risk-tier audit rule below.
8. Correct findings through the same GitHub-centered branch. A changed head invalidates all stale audit evidence.
9. Immediately before merge, re-fetch the Pull Request, head SHA, scope, hold state, required checks, audit evidence, and mergeability.
10. Merge only with expected-head-SHA protection.

## Risk tiers and audit use

### Low risk

Examples: documentation, text-only guidance, formatting, tests-only changes, and generated metadata with no executable, workflow, permission, authentication, deployment, or destructive effect.

Required:

- bounded trusted Issue scope;
- exact-head required checks;
- coordinator review of the GitHub-visible diff;
- final live recheck and expected-head merge protection.

External Codex or Claude audit is optional. Provider availability alone must not block low-risk completion.

### Standard risk

Examples: ordinary application or Foundation code that does not touch a protected category.

Required:

- all low-risk safeguards;
- one clean independent exact-SHA audit from **Codex or Claude**;
- no routine second-provider audit for the same head.

### Protected risk

Protected categories include workflows, permissions, authentication or Secret interfaces, supervisor and security policy, repository settings, billing, deployment or production, and destructive data operations.

Required:

- explicit protected authorization in the trusted Issue;
- all exact-head checks and product checks;
- one clean independent exact-SHA audit from **Codex or Claude**;
- final live recheck and expected-head merge protection.

When neither provider can produce valid audit evidence, protected merge waits. A second provider is used only when the first reports an unresolved blocker, the owner explicitly requests a second opinion, or a documented exceptional-risk rule requires it.

## Auditor selection and quota control

- Select one auditor for each exact SHA: `codex` or `claude`.
- Prefer a provider with usable capacity and a working repository connection.
- A quota, setup, connection, or generic error response is `route-unavailable`, not audit evidence.
- When the selected route becomes unavailable before valid audit completion, the coordinator may switch once to the other provider.
- After one provider returns a valid clean audit for the exact head, do not request the other provider routinely.
- Any head change invalidates the previous audit. Request one fresh audit from one selected provider, not both.
- Do not repeatedly post an identical provider request.

## GitHub-visible completion rule

Local agent output is advisory. A branch write, commit, correction, or completion exists only when connected GitHub evidence confirms the expected repository, branch, and exact remote SHA. Provider-reported commits that were not pushed never satisfy implementation, audit, or merge gates.

## Required status fields

The Pull Request body or machine-readable status must record:

- implementation route: `github-direct` or the named exceptional fallback;
- exact GitHub-visible head SHA;
- risk tier: `low`, `standard`, or `protected`;
- selected auditor: `none`, `codex`, or `claude`;
- audit state: `not-required`, `required`, `pending`, `clean`, `blocked`, or `route-unavailable`;
- required checks and current conclusions;
- next automatic action.

## Invariants that are never relaxed

- no automation direct push to the default branch;
- one trusted owner-authored bounded scope;
- same-repository provenance;
- exact GitHub-visible remote SHA for checks, audit, and merge;
- no Secret value access, output, persistence, copying, or inference;
- no proposed-branch execution in a job carrying Secrets, OIDC, or repository write permission;
- no arbitrary repository, ref, workflow, path, or command selected from untrusted content;
- explicit authorization for protected operations;
- required Foundation and product checks;
- one final live recheck;
- expected-head-SHA merge protection;
- no deployment or production mutation without separate explicit authorization.

## Human-action boundary

Provider quota exhaustion is not a request for the owner to perform routine implementation, review, retry, or merge work. The coordinator continues through GitHub-direct work, uses the other auditor only when the tier requires it and the first route is unavailable, or records a non-notifying blocked state until required protected audit capacity returns.

Human notice remains limited to genuine account, credential, MFA, CAPTCHA, hardware-key, trusted-device, or disconnected-integration UI operations that cannot be completed through connected capabilities.

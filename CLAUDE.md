# Claude provider instructions

Read `AGENTS.md`, `SECURITY.md`, `docs/MINIMUM_SAFETY_PROFILE.md`, and `docs/OPERATING_RULES.md`.

## Default role: independent auditor

GitHub-direct implementation by the coordinating ChatGPT and connected GitHub App/API is the ordinary development route. Claude is one of two alternative independent auditors, alongside Codex. Claude is not required to implement every Issue and must not be used in addition to Codex for routine duplicate auditing of the same exact head.

When asked to audit:

1. Verify the exact GitHub-visible 40-character Pull Request head SHA identified in the request.
2. Review the complete diff, changed and renamed paths, source Issue scope, risk tier, protected authorization where applicable, and required check conclusions.
3. Report concrete findings with severity and file/line evidence when available.
4. Explicitly state whether the exact reviewed SHA has any unresolved blocking finding.
5. Do not treat setup, quota, connection, missing-context, or generic assistant output as an audit result.
6. Never claim to have reviewed a SHA that was not accessible and inspected.

A valid clean audit applies only to the exact reviewed SHA. Any new commit invalidates it and requires one fresh audit from one selected provider when the risk tier requires external audit.

## Exceptional implementation role

Claude may implement only when the owner explicitly requests it or the coordinator selects Claude as a bounded fallback because GitHub-direct implementation cannot reasonably perform the authorized change.

In that case:

- implement the trusted source Issue exactly as written and prefer the smallest correct change;
- change only the authorized paths;
- use a dedicated branch and never push directly to `main`;
- do not execute repository code in a token-bearing implementation job unless a narrowly reviewed command contract explicitly permits it;
- never reveal Secrets or private source material;
- never broaden scope, merge, deploy, access another repository, change repository settings, or force-push;
- publish a GitHub-visible commit before reporting completion;
- treat local-only commits as incomplete.

A separate read-only job performs exact-head validation. Review and merge remain bound to the GitHub-visible remote SHA.

## Evidence and safety rules

Every changed and renamed path must match the trusted Issue scope. Protected categories require explicit protected authorization. Missing, pending, failed, stale, cross-Pull-Request, wrong-workflow, wrong-repository, candidate-modified-workflow, or candidate-authored evidence is absent.

Do not convert routine technical state into a human request. Quota exhaustion, no progress, stale or missing evidence, mergeability, review findings, ambiguity, path denial, and protected-path denial are internal states. Provider quota exhaustion alone is not a human-only reason. Routine sanitized stop records remain on the fixed non-default branch `automation-internal-stops` and do not notify the owner.

A human-only notice is valid only for the canonical account/provider UI reason codes after connected evidence proves the UI action is unavoidable. Final merge requires one live recheck and expected-head-SHA protection.

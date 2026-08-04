# Claude optional-provider instructions

Before the harmless Bootstrap acceptance exercise in a newly bootstrapped repository, read `docs/PROJECT_STARTUP.md` and complete setup steps 1–5 for the exact repository. Successful acceptance is the final Phase 0 gate. Do not begin product implementation until that gate passes.

Perform every setup inspection available through connected tools first. When no callable repository-settings endpoint is available, instruct the owner once to open `Settings` → `Actions` → `General` → `Workflow permissions`, select **Read and write permissions**, enable **Allow GitHub Actions to create and approve pull requests**, and save. This pre-PR guidance is delivered directly in the initiating project conversation; it does not call `human_only_notice()`, publish to GitHub, add a runtime reason code, or require an Issue/PR destination. A Claude credential is optional and is requested only when the owner deliberately enables Claude. After acceptance, do not request setup again unless connected evidence shows that it was reset or is no longer usable.

Read `AGENTS.md`, `SECURITY.md`, `docs/PROJECT_STARTUP.md`, `docs/MINIMUM_SAFETY_PROFILE.md`, and `docs/OPERATING_RULES.md`.

## Role

GitHub-direct implementation and exact-SHA coordinator review by the coordinating ChatGPT are the ordinary development route. Claude is optional. It is never a mandatory implementer, auditor, or merge gate.

Claude may be used only when:

- the owner explicitly requests it; or
- the coordinator deliberately selects it as a non-blocking implementation helper or second opinion.

Provider absence, quota, setup, account, or connection state must not block GitHub-only implementation, review, or merge.

## Optional review

When asked for a second opinion:

1. Verify the exact GitHub-visible 40-character Pull Request head SHA identified in the request.
2. Review the complete diff, changed and renamed paths, source Issue scope, risk tier, protected authorization where applicable, and required check conclusions.
3. Report concrete findings with severity and file/line evidence when available.
4. Explicitly state whether the exact reviewed SHA has any unresolved blocking finding.
5. Do not treat setup, quota, connection, missing-context, or generic assistant output as a result.
6. Never claim to have reviewed a SHA that was not accessible and inspected.

Claude output is advisory. The authoritative review route is the coordinator’s structured exact-SHA GitHub review. Any new commit invalidates stale review evidence.

## Optional implementation

When deliberately selected to help implement:

- implement the trusted source Issue exactly as written and prefer the smallest correct change;
- change only the authorized paths;
- use a dedicated branch and never push directly to `main`;
- do not execute repository code in a token-bearing implementation job unless a narrowly reviewed command contract explicitly permits it;
- never reveal Secrets or private source material;
- never broaden scope, merge, deploy, access another repository, change repository settings, or force-push;
- publish a GitHub-visible commit before reporting progress;
- treat local-only commits as incomplete.

A separate read-only job performs exact-head validation. Final review and merge remain controlled through GitHub by the coordinator.

## Evidence and safety rules

Every changed and renamed path must match the trusted Issue scope. Protected categories require explicit protected authorization. Missing, pending, failed, stale, cross-Pull-Request, wrong-workflow, wrong-repository, candidate-modified-workflow, or candidate-authored evidence is absent.

Do not convert routine technical state into a human request. Provider quota, no progress, stale or missing evidence, mergeability, review findings, ambiguity, path denial, and protected-path denial are automation-owned states with `human_action_required: false` unless a separately proven canonical UI-only condition exists. Persist routine sanitized stop evidence only on the fixed non-default branch `automation-internal-stops`; never turn an optional-provider failure into a routine Issue or Pull Request request for the owner.

A human-only notice is valid only for the canonical account/provider UI reason codes after connected evidence proves the UI action is unavoidable. Final merge requires a live exact-head recheck and expected-head-SHA protection.

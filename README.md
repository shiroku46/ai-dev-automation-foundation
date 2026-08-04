# AI Development Automation Foundation

A public, reusable, history-free foundation for guarded AI-assisted development centered on GitHub.

## Default operating model

The coordinating ChatGPT uses the connected GitHub App/API as the ordinary implementation, publication, review, correction, and merge-orchestration route. It creates a dedicated branch, changes only Issue-authorized files, creates GitHub-visible commits, opens or updates one Pull Request, inspects the exact diff, and confirms the remote head SHA.

GitHub Actions performs exact-head Foundation and product checks. The coordinating ChatGPT records a structured review for that exact GitHub-visible SHA. Codex and Claude are optional helpers or second-opinion reviewers only; neither provider is required for implementation, review, or merge.

See `docs/MINIMUM_SAFETY_PROFILE.md` and `docs/OPERATING_RULES.md` for the authoritative flow and risk tiers.

## What this repository provides

- a GitHub-centered branch, Pull Request, exact-SHA, coordinator-review, and guarded-merge operating contract;
- read-only CI that is safe for forked pull requests;
- optional bounded provider routes that never become mandatory dependencies;
- risk-tiered exact-SHA coordinator review, including two-pass review for protected work;
- bounded reconciliation for missing trusted checks;
- a deterministic recovery and merge decision engine;
- a default-branch-controlled supervisor that never executes proposed-branch code with write permissions;
- a Bootstrap generator for installing the same controls in another repository;
- security, export, workflow, and policy regression tests.

## Safety model

Untrusted pull-request code runs only in jobs with `contents: read` and without Secrets, OIDC, or write permissions. Jobs that can comment, relabel, dispatch, close, mark ready, or merge operate only from the default branch, inspect immutable current SHAs, require same-repository provenance, use fixed workflow names and refs, bound their candidate set, and use an expected-head-SHA merge guard.

No automation writes directly to the default branch. Every changed and renamed path must match a trusted owner-authored Issue scope. Workflows, permissions, authentication or Secret interfaces, supervisor/security policy, repository settings, billing, deployment/production, and destructive operations require explicit protected authorization.

Local or provider-reported commits are not completion until GitHub confirms the expected remote branch SHA. Provider quota, setup, or connection responses are optional-route diagnostics, not review evidence, and never delegate routine development work to the owner.

## Mandatory repository Phase 0

Before the harmless Bootstrap acceptance exercise in a newly bootstrapped repository, complete setup steps 1–5 in [`docs/PROJECT_STARTUP.md`](docs/PROJECT_STARTUP.md). Successful acceptance is the final Phase 0 gate; product Issues and implementation start only afterward.

The exact target repository must have connected GitHub access, enabled Actions/Foundation workflows, and the following GitHub setting before acceptance:

`Settings` → `Actions` → `General` → `Workflow permissions`

- select **Read and write permissions**;
- enable **Allow GitHub Actions to create and approve pull requests**;
- save the setting.

Codex and Claude setup is optional and is required only when the owner deliberately enables that provider route. GitHub-only acceptance and development must not wait for a provider environment, credential, or quota. After the exact repository passes acceptance and completes Phase 0, do not request setup again unless connected evidence shows the setting or integration is no longer usable.

## Validation

```bash
python scripts/public_export_guard.py .
python scripts/validate_repository.py
python -m unittest discover -s tests
```

## Bootstrap

```bash
python bootstrap/generator.py --target ../example-repository --owner YOUR_GITHUB_LOGIN
```

Review the generated install checklist before enabling write-capable workflows. Repository Secrets are never generated, copied, or printed.

## Project status

This public repository is the implementation source of truth. Earlier private sandboxes remain archives and are not imported into this history.

# AI Development Automation Foundation

A public, reusable, history-free foundation for guarded AI-assisted development centered on GitHub.

## Default operating model

The coordinating ChatGPT uses the connected GitHub App/API as the ordinary implementation route. It creates a dedicated branch, changes only Issue-authorized files, creates GitHub-visible commits, opens or updates a Pull Request, inspects the exact diff, and confirms the remote head SHA.

GitHub Actions performs exact-head Foundation and product checks. When an external audit is required, **one** available provider—Codex **or** Claude—reviews the exact GitHub-visible SHA. Routine dual-provider auditing is not required. Low-risk documentation, formatting, tests-only, and non-executable metadata changes may complete with exact-head checks and coordinator diff review.

See `docs/MINIMUM_SAFETY_PROFILE.md` and `docs/OPERATING_RULES.md` for the authoritative flow and risk tiers.

## What this repository provides

- a GitHub-centered branch, Pull Request, exact-SHA, and guarded-merge operating contract;
- read-only CI that is safe for forked pull requests;
- optional bounded provider implementation routes for exceptional fallback work;
- single-provider exact-SHA audit policy using Codex or Claude;
- bounded reconciliation for missing trusted checks;
- a deterministic recovery and merge decision engine;
- a default-branch-controlled supervisor that never executes proposed-branch code with write permissions;
- a Bootstrap generator for installing the same controls in another repository;
- security, export, workflow, and policy regression tests.

## Safety model

Untrusted pull-request code runs only in jobs with `contents: read` and without Secrets, OIDC, or write permissions. Jobs that can comment, relabel, dispatch, close, mark ready, or merge operate only from the default branch, inspect immutable current SHAs, require same-repository provenance, use fixed workflow names and refs, bound their candidate set, and use an expected-head-SHA merge guard.

No automation writes directly to the default branch. Every changed and renamed path must match a trusted owner-authored Issue scope. Workflows, permissions, authentication or Secret interfaces, supervisor/security policy, repository settings, billing, deployment/production, and destructive operations require explicit protected authorization.

Provider-reported local commits are not completion until GitHub confirms the expected remote branch SHA. Provider quota, setup, or connection responses are not audit evidence and do not delegate routine development work to the owner.

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

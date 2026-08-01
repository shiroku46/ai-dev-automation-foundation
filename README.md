# AI Development Automation Foundation

A public, reusable foundation for practical AI-assisted development on GitHub. The current design favors a small, understandable minimum safety baseline over maximum-defense gates that repeatedly block ordinary work.

## What this repository provides

- a Phase 0 startup contract for GitHub/Codex authorization, exact-repository Codex Environment creation, Claude credential installation, and harmless acceptance testing;
- an owner-authorized Claude Issue Queue;
- read-only exact-SHA Foundation checks that do not expose Secrets or write permission to proposed code;
- one machine-readable `foundation-task-scope` contract for paths, risk, operation, prohibited effects, and required checks;
- low, standard, and protected review tiers;
- exact GitHub-visible remote-head verification and expected-head merge protection;
- a default-branch-controlled coordinator and bounded fallback/recovery path;
- a Bootstrap generator for installing the same policy into another repository;
- export, workflow, policy, and regression tests.

## Minimum safety model

The required boundaries are:

- no automation direct push to the default branch;
- exact GitHub-visible remote SHA for checks, review, readiness, and merge;
- one trusted owner-authored scope covering every changed and renamed path;
- protected risk for workflows, permissions, authentication/Secret interfaces, supervisor/security policy, settings, billing, deployment/production, or destructive operations;
- no Secret value exposure and no candidate code in Secret/OIDC/write-capable jobs;
- same-repository and fixed-workflow boundaries;
- exact-head Foundation and configured product checks;
- one final live recheck and expected-head merge protection;
- separate explicit authorization for deployment and production mutation.

See [`docs/MINIMUM_SAFETY_PROFILE.md`](docs/MINIMUM_SAFETY_PROFILE.md) and [`docs/OPERATING_RULES.md`](docs/OPERATING_RULES.md).

## Review tiers

- **Low risk:** scope and required exact-head checks; Codex is optional.
- **Standard risk:** clean exact-SHA Codex or a trusted unedited coordinator review for the same SHA.
- **Protected risk:** clean exact-SHA Codex requested through an owner/connector-supported route.

GitHub Actions records a neutral review-required state and does not depend on a bot-authored provider mention. Provider setup guidance is not review evidence.

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

Review the generated installation checklist. Complete Phase 0 before the first product Issue. Repository Secret values are never generated, copied, printed, or stored by the Foundation.

## Project status

This public repository is the implementation source of truth. The public E2E repository is the release-acceptance source. Earlier private sandboxes and superseded maximum-defense procedures are archives only.

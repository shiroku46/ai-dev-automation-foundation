# Foundation release acceptance

## Accepted source

- repository: `shiroku46/ai-dev-automation-foundation`
- accepted default-branch source SHA: `04e905ac651e8c8c9509406ac172919fd5ba7c01`
- implementation route: GitHub-direct
- optional provider availability: nonblocking
- human_action_required: `false`

This repository and its reviewed default branch are the implementation source of truth for the Foundation.

## Source and generated-target validation

The accepted source passed the public export guard, Repository validator and complete Unit Test discovery on its exact Pull Request heads. The Unit Test suite renders fresh generated targets and verifies their public export and generated-target Repository validator without copying Foundation development tests.

Bootstrap coverage includes both an empty `new-repository` target and an `existing-product` target. It computes and rechecks the complete plan before writing, rejects collisions, moved state, unsafe paths and symlinks, preserves existing README, LICENSE and agent/security instructions, and never requires a temporary default-branch workflow, PAT, force update or provider credential.

Each generated target receives `FOUNDATION.lock.json` with exact source SHA and managed-file hashes. `scripts/foundation_drift.py` reports unchanged, modified, missing, stale, new and collision states. The target-owned `.github/foundation-product-checks.json` is validated and preserved outside the Foundation lock.

## Runtime acceptance

Queue admission uses bounded scalar GitHub event context through `scripts/queue_event_guard.py`; it does not read `github.event_path`. The optional provider route has no provider invocation, Secret, OIDC, checkout, repository credential or repository-write capability. Provider absence, setup state and quota cannot block GitHub-direct implementation, review or merge.

The GitHub Coordinator uses one trusted owner-authored source Issue, exact changed and renamed paths, protected authorization, sole-candidate collision checks, immutable exact-head native checks, default-branch-controlled product workflow configuration, PR-associated product runs, coordinator reviews, zero unresolved threads, final live state rechecks and expected-head-SHA merge.

GitHub resources are treated as finite. The Foundation includes bounded API governance, rate-limit and circuit-breaker states, request budgets, deterministic retry identities, capacity/collision records and Fleet progress collection.

## Live merge evidence

- PR #206: Queue admission extraction and legacy runtime retirement; expected-head candidate `6abb82cecac1a1433381a76ca4bd082bffd5dee9`; merge commit `0ce98273cbb8a32cb729bfc833d009609afd6db9`.
- PR #208: default-branch product workflow validation; expected-head candidate `a6ac637995a661cdf700577eca40ea56588e4116`; exact-head CI run `30994340177`; Unit Tests run `30994340151`; merge commit `04e905ac651e8c8c9509406ac172919fd5ba7c01`.

Both candidates used bounded source Issues, exact scopes, clean coordinator review evidence, zero unresolved threads, sole-candidate collision rechecks and expected-head merge protection.

## Completion state

No repository implementation blocker remains in the accepted Foundation scope. Future work consists of applying the versioned Bootstrap or upgrade plan to product repositories and supplying each product repository's own lint, test, build and type-check workflows through its target-owned product-check configuration.
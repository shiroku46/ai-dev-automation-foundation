# GitHub capacity and collision governance

This document defines the Phase A contract for GitHub-centered automation. GitHub is a finite execution and storage platform. API calls, Actions minutes and concurrency, artifacts, caches, repository history, individual files, Git LFS, Packages, Codespaces, larger runners, paid storage, and settings-controlled features are all policy-governed resources.

The contract is descriptive and machine-readable through `docs/GITHUB_CAPACITY_STATE.schema.json`. Runtime enforcement belongs to later, separately authorized child Issues.

## Non-negotiable safety boundary

Resource pressure never weakens a required exact-head check, trusted-source rule, authorized path boundary, protected authorization, unresolved-thread gate, collision check, final live recheck, or expected-head merge. Noncritical automation is deferred with `human_action_required: false`.

Human action is reserved for a proven owner-only boundary:

- repository or integration connection UI;
- repository setting or workflow-permission UI;
- billing or paid-feature approval;
- credential or environment approval that cannot be completed through an already connected automation route;
- account-level repository creation UI.

Capacity state and logs contain public-safe metadata only. Secret values, tokens, credential material, derived hashes of secrets, authorization headers, private prompts, and provider output are excluded.

## State model

### API state

- `normal`: the current pass is within its bounded request budget and has no connected rate-limit or platform blocker.
- `throttled`: a primary or secondary limit supplies a retry/reset boundary; noncritical reads are deferred.
- `circuit-open`: repeated retryable failures reached the configured threshold. New noncritical API work stops until the bounded reopen time.
- `unavailable`: connected API evidence is currently unavailable or malformed, so security-relevant decisions fail closed.

Each pass records a strict request budget, requests used, attempts, retry/reset timestamps when connected, and consecutive retryable failures. Retryable reads use bounded exponential backoff with jitter and honor server-provided retry/reset information when available. Authentication, permission, primary-limit, secondary-limit, transport, platform-outage, malformed-response, and incomplete-pagination failures are distinct states.

Non-idempotent writes are never replayed blindly. Before a retry, the coordinator re-fetches connected state and proves whether the intended effect already occurred. Complete pagination is mandatory when completeness affects authorization, duplicate prevention, collision detection, review threads, checks, or merge readiness. Conditional reads may reduce cost only where stale evidence cannot affect a trust decision.

### Actions state

- `normal`: configured soft budgets and required validation capacity are available.
- `soft-budget-near`: the repository is approaching a configured soft run, duration, or artifact budget.
- `deferred`: noncritical work is paused without creating an owner task.
- `blocked`: a required run cannot proceed without a proven owner-only setting, billing, credential, or environment action.

Workflows use deterministic concurrency identities, explicit timeouts, bounded retry counts, and duplicate suppression. Superseded read-only runs may be cancelled when the newer head makes them obsolete. Publication, audit persistence, and merge mutations are not cancelled in a way that leaves ambiguous partial state. Protected/runtime/workflow/authentication changes always run their full required suite.

### Artifact and storage state

- `normal`: observed repository, file, cache, and artifact use is within policy.
- `near-policy-limit`: a soft policy threshold is approaching; noncritical production is reduced or deferred.
- `blocked`: publication would violate a size, type, retention, billing, or storage rule.

Normal Git history does not contain model weights, datasets, bulk audio, bulk images, video, virtual environments, dependency directories, caches, or normal build output. Git contains source and minimal deterministic fixtures. Checkpoint artifacts are digest-bound, minimal, purpose-labeled, size-bounded, and retention-bounded. Enabling LFS, Packages, Releases as storage, Codespaces, larger runners, paid storage, or another billing-sensitive feature requires a separate protected authorization.

## Collision identity and ownership

Before implementation, publication, recovery, upgrade, or merge, the coordinator derives the exact authorized path set and reads connected state.

A collision identity contains:

- repository;
- source Issue and request fingerprint;
- active branch and intended Pull Request;
- exact default and candidate SHAs;
- current changed paths and every `previous_filename` from renames;
- protected path family ownership;
- Queue/recovery/Bootstrap identity where applicable.

One request identity owns at most one active implementation branch and one intended candidate Pull Request, except for a separately authorized exact-base integration mode. Protected path families such as `.github/workflows/**`, `bootstrap/**`, supervisor/runtime modules, policy files, and validators are reserved to one live candidate at a time.

An intersecting path set blocks a second publication unless the new Issue declares an explicit dependency or integration relationship to the exact blocking PR and head. Both the rename source and destination are occupied. Parallel work is allowed only for provably disjoint paths and independent mutable resources.

When a collision appears after work starts, publication or merge stops. The automation preserves the GitHub-visible checkpoint and records the exact blocking PR and path intersection with `human_action_required: false`. It does not force-update, overwrite, automatically rebase conflicting candidates, merge conflicting work, or replay a write. After the blocker merges or closes, recovery re-reads the new default head and uses a separately authorized path.

## Event-first operation and polling

Trusted Issue, Pull Request, workflow-completion, and check-completion events are preferred. Polling exists only as a bounded watchdog with one documented cadence, no-change suppression, deterministic concurrency, and idempotent records. Repeated identical comments, labels, dispatches, branches, Pull Requests, and status records are suppressed.

While the API circuit is open or a soft budget is exhausted, noncritical polling and optional providers are deferred. Required evidence is not replaced with stale data.

## Capacity record

The machine-readable record is idempotent and public-safe. It records:

- schema version and repository;
- observation timestamp;
- exact default SHA and nullable candidate SHA;
- API state and bounded request metadata;
- Actions state, soft budgets, and active concurrency identity;
- artifact/storage state and observed usage;
- collision state with exact blocking PRs and paths;
- next automatic action;
- `human_action_required` and a nullable canonical human action.

A clear collision has empty blocking arrays. A blocked collision has at least one blocking PR and one blocking path. `canonical_human_action` is null unless `human_action_required` is true; when true, the action kind is one of `settings`, `billing`, `credential`, `environment`, or `repository-creation`.

## Phase handoff

- **Phase B:** one reviewed API-governance client, bounded retry/reset/circuit behavior, request budgets, and read-only collision discovery before mutations.
- **Phase C:** separately authorized workflow concurrency, cancellation, timeout, artifact retention, soft budgets, and Bootstrap propagation.
- **Phase D:** separately authorized repository size/type guard and optional fleet reporting after the dashboard contract is stable.

Each phase uses non-overlapping child Issues, complete connected-state collision preflight, exact-head checks, coordinator review, final live recheck, and expected-head merge.
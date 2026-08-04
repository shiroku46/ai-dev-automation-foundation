# Fleet Progress Dashboard

The Fleet Progress Dashboard makes GitHub—not chat transcripts or provider reports—the durable source of project progress. A trusted coordinator can validate explicit schema-version-2 JSON records, render deterministic Markdown, or use the protected read-only collector to derive those records from bounded live GitHub evidence.

The renderer performs no network request or GitHub mutation. The collector performs no GitHub mutation, Project synchronization, workflow dispatch, repository-setting change, deployment, billing, production, or destructive operation.

## Authoritative evidence

Construct records from live GitHub evidence:

- trusted source Issue and bounded scope;
- Pull Request number and current GitHub-visible head SHA;
- exact-head Foundation and product checks;
- structured `github-coordinator` review state;
- exact unresolved review-thread count;
- current blocker, next automatic action, and genuine human-action boundary.

A chat summary, provider-reported local commit, provider quota message, or unverified branch name is not completion evidence. Codex and Claude may appear only as optional implementation-route metadata; they are never review or merge dependencies.

## Schema version 2

The top-level object contains exactly:

```json
{
  "schema_version": 2,
  "generated_at": "2026-08-04T09:05:00Z",
  "projects": []
}
```

Each project contains exactly:

```json
{
  "repository": "owner/repository",
  "phase": "Phase 2",
  "issue": 160,
  "pull_request": 158,
  "status": "ready_to_merge",
  "head_sha": "0123456789abcdef0123456789abcdef01234567",
  "checks": {
    "CI": "success",
    "Unit Tests": "success"
  },
  "implementation_route": "github-direct",
  "risk_tier": "protected",
  "review_route": "github-coordinator",
  "review_state": "clean",
  "unresolved_review_threads": 0,
  "next_action": "Merge with expected-head protection",
  "blocker": null,
  "human_action_required": false,
  "updated_at": "2026-08-04T09:04:00Z"
}
```

### Bounded values

`status`:

- `backlog`
- `ready`
- `implementing`
- `pr_open`
- `ci_running`
- `review_required`
- `fix_required`
- `human_action`
- `blocked`
- `ready_to_merge`
- `completed`
- `idle`

`implementation_route`:

- `github-direct`
- `codex-optional`
- `claude-optional`

`risk_tier`:

- `low`
- `standard`
- `protected`

`review_route` is exactly `github-coordinator`.

`review_state`:

- `required`
- `pending`
- `clean`
- `blocked`

`unresolved_review_threads` is a non-negative integer obtained from the live Pull Request review-thread state.

Check conclusions:

- `queued`
- `in_progress`
- `success`
- `failure`
- `cancelled`
- `skipped`
- `neutral`
- `timed_out`
- `action_required`
- `stale`
- `missing`

Schema version 1 and the former `selected_auditor` / `audit_state` fields are rejected. Provider availability is not a merge-readiness condition.

## Fail-closed relationships

- active Pull Request and merge-readiness statuses require a lowercase 40-character exact head SHA;
- `pending`, `clean`, and `blocked` review states require an exact head SHA;
- `review_route` must be `github-coordinator`;
- `unresolved_review_threads` must be present, non-negative, and not a boolean;
- `review_state: clean` requires `unresolved_review_threads: 0`;
- `fix_required`, `human_action`, `blocked`, and blocked review require a nonempty blocker;
- `ready_to_merge`, `completed`, and `idle` require a null blocker;
- `human_action_required` is true exactly for `status: human_action`;
- `ready_to_merge` requires at least one check, every check passing, `review_state: clean`, `unresolved_review_threads: 0`, no blocker, and no human action;
- optional provider implementation routes do not change section placement or review requirements;
- unknown fields, duplicate JSON keys, duplicate repositories, malformed timestamps, invalid SHAs, excessive input size, excessive projects, and excessive checks fail closed.

## Dashboard sections

The renderer produces, in priority order:

1. Human Action Required
2. Blocked
3. Active Implementation and Review
4. Ready to Merge
5. Completed or Idle

The Review column displays the review route, review state, and unresolved-thread count. Provider quota or route unavailability alone never creates a blocked or human-action entry.

## Offline renderer commands

Validate without rendering or writing:

```bash
python scripts/fleet_progress.py fleet.json --check
```

Render to stdout:

```bash
python scripts/fleet_progress.py fleet.json
```

Write to one explicit output path:

```bash
python scripts/fleet_progress.py fleet.json --output docs/FLEET_STATUS.md
```

`--check` cannot be combined with `--output`. Without `--output`, the command creates no file.

## Read-only GitHub collector

`scripts/fleet_collect_github.py` validates one bounded configuration and emits a schema-version-2 Fleet Progress document. It derives current PR/head state, exact-head workflow conclusions, trusted coordinator-review markers, and authoritative unresolved review-thread count.

### Collector configuration

```json
{
  "schema_version": 2,
  "projects": [
    {
      "repository": "owner/repository",
      "phase": "Phase 2",
      "issue": 160,
      "pull_request": 158,
      "required_workflows": ["CI", "Unit Tests"],
      "implementation_route": "github-direct",
      "risk_tier": "protected",
      "trusted_coordinators": ["owner-login"],
      "next_action": "Merge with expected-head protection",
      "blocker": null,
      "human_action_required": false,
      "baseline_status": null
    }
  ]
}
```

For a project with no Pull Request, set `pull_request` to null, `required_workflows` to an empty array, and `baseline_status` to `backlog`, `ready`, or `idle`. Non-PR entries perform no network request.

`selected_auditor` and `audit_state` are invalid configuration fields. `trusted_coordinators` identifies GitHub logins whose unedited exact-head coordinator-review markers may count.

### Coordinator markers

Low and standard clean review:

```text
<!-- foundation-coordinator-review:<40-character-sha>:clean -->
```

Protected clean review requires both:

```text
<!-- foundation-coordinator-review:<40-character-sha>:scope-security:clean -->
<!-- foundation-coordinator-review:<40-character-sha>:correctness-race:clean -->
```

Corresponding `blocked` markers produce `review_state: blocked`. Markers count only when authored by a configured trusted coordinator, unedited, bound to the exact current head, and accompanied by a nonempty review summary. Stale, edited, untrusted, content-free, malformed, or duplicate-ambiguous markers do not produce clean state.

### Collector commands

Validate configuration without a network request or output write:

```bash
python scripts/fleet_collect_github.py fleet-config.json --check-config
```

Collect to stdout:

```bash
GH_TOKEN=... python scripts/fleet_collect_github.py fleet-config.json
```

Write to one explicit output path:

```bash
GH_TOKEN=... python scripts/fleet_collect_github.py fleet-config.json --output fleet.json
```

`GH_TOKEN` or `GITHUB_TOKEN` is required only for Pull Request records because authoritative review-thread state is read through GitHub GraphQL. If both variables are set, their values must match. Token values are used only in Authorization headers and are never printed, hashed, returned, or persisted.

### Fixed network boundary

The collector constructs only:

- HTTPS GET `/repos/{owner}/{repository}/pulls/{positive-number}` with no query;
- HTTPS GET `/repos/{owner}/{repository}/actions/runs` with exactly `event=pull_request`, validated exact `head_sha`, `per_page=100`, and bounded `page`;
- HTTPS POST `https://api.github.com/graphql` containing one source-code-constant read-only query for the same repository/PR review threads and top-level comments.

The GraphQL source contains no mutation and cannot be selected or modified through configuration, CLI input, environment variables, Issue text, or API responses. Variables are limited to validated owner, repository, positive PR number, and bounded response-provided pagination cursors.

Workflow and review pagination is complete and bounded. Repository, PR, head, event, workflow-run repository, comment author, timestamps, cursors, page counts, node counts, and response sizes are validated. Incomplete, moved, mismatched, duplicate, malformed, or excessive evidence fails closed.

The collector never prints or hashes response bodies. Errors expose only bounded classifications and never token or response content.

### Conservative state derivation

- failed required checks produce `fix_required`;
- missing or `action_required` checks produce `blocked`;
- pending checks produce `ci_running`;
- a current-head blocked coordinator marker produces `blocked` with `human_action_required: false`;
- missing, incomplete, ambiguous, or unresolved review evidence produces `review_required`;
- draft PRs with clean review remain `pr_open`;
- non-draft PRs become `ready_to_merge` only with all checks passing, clean risk-tier-required coordinator review, and zero unresolved threads;
- optional provider routes never change review state, blocker, or human-action state;
- every generated document is validated through `fleet_progress.py` before stdout or explicit output write.

## Security and operational boundary

- renderer input is limited to 2 MiB;
- collector configuration is limited to 256 KiB;
- project, workflow, coordinator, page, node, response, and check counts are bounded;
- duplicate JSON keys are rejected before validation;
- errors do not echo input records, response bodies, or credential values;
- no token, Secret, arbitrary environment variable, command, URL, endpoint, repository, ref, workflow, provider, method, header, or GraphQL source is selected from untrusted input;
- Markdown cells escape pipes and backslashes;
- GitHub Issues, Pull Requests, exact-head checks, coordinator-review records, unresolved-thread state, and exact remote SHAs remain authoritative after collection and rendering.

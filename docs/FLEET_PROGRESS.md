# Fleet Progress Dashboard

The Fleet Progress Dashboard makes GitHub—not chat transcripts or provider reports—the durable source of project progress. This phase is an offline, read-only renderer: a trusted coordinator supplies explicit schema-version-2 JSON records, the CLI validates them fail-closed, and deterministic Markdown presents one cross-repository view.

This command performs no network request, GitHub mutation, Project synchronization, workflow dispatch, environment inspection, or implicit file write.

## Authoritative evidence

Construct records from live GitHub evidence:

- trusted source Issue and bounded scope;
- Pull Request number and current GitHub-visible head SHA;
- exact-head Foundation and product checks;
- structured `github-coordinator` review state;
- unresolved review-thread count reflected in the status decision;
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
- `fix_required`, `human_action`, `blocked`, and blocked review require a nonempty blocker;
- `ready_to_merge`, `completed`, and `idle` require a null blocker;
- `human_action_required` is true exactly for `status: human_action`;
- `ready_to_merge` requires at least one check, every check passing, `review_state: clean`, no blocker, and no human action;
- optional provider implementation routes do not change section placement or review requirements;
- unknown fields, duplicate JSON keys, duplicate repositories, malformed timestamps, invalid SHAs, excessive input size, excessive projects, and excessive checks fail closed.

## Dashboard sections

The renderer produces, in priority order:

1. Human Action Required
2. Blocked
3. Active Implementation and Review
4. Ready to Merge
5. Completed or Idle

Provider quota or route unavailability alone never creates a blocked or human-action entry.

## Commands

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

## Security and operational boundary

- input is limited to 2 MiB;
- project and check counts are bounded;
- duplicate JSON keys are rejected before validation;
- errors do not echo input records or credential values;
- no token, Secret, environment variable, command, URL, endpoint, repository, ref, or provider is selected from input;
- Markdown cells escape pipes and backslashes;
- GitHub Issues, Pull Requests, exact-head checks, coordinator-review records, and exact remote SHAs remain authoritative after rendering.

A separate protected phase may collect these records from fixed read-only GitHub API endpoints. It must emit schema version 2 and preserve the same GitHub-only review policy.

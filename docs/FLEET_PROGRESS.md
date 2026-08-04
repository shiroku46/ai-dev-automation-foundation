# Fleet Progress Dashboard

The Fleet Progress Dashboard makes GitHub—not a chat transcript—the durable source of project progress. The first phase is an offline, read-only renderer: a trusted coordinator supplies explicit JSON records, the CLI validates them fail-closed, and the resulting Markdown gives one cross-repository overview.

This phase does **not** query GitHub, mutate a repository, synchronize GitHub Projects, or schedule refreshes. Those capabilities require separate protected authorization.

## Status source

Use GitHub Issues, Pull Requests, exact remote SHAs, check conclusions, and audit evidence to construct the input. Do not treat a provider-reported local commit, a chat summary, or an unverified branch name as completion evidence.

The input document has this form:

```json
{
  "schema_version": 1,
  "generated_at": "2026-08-04T05:30:00Z",
  "projects": [
    {
      "repository": "owner/example",
      "phase": "Phase 1",
      "issue": 154,
      "pull_request": 155,
      "status": "review_required",
      "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "checks": {
        "CI": "success",
        "Unit Tests": "success"
      },
      "implementation_route": "github-direct",
      "risk_tier": "standard",
      "selected_auditor": "codex",
      "audit_state": "pending",
      "next_action": "Complete one exact-SHA audit",
      "blocker": null,
      "human_action_required": false,
      "updated_at": "2026-08-04T05:25:07Z"
    }
  ]
}
```

## Bounded values

`status` accepts:

- `backlog`, `ready`, `implementing`;
- `pr_open`, `ci_running`, `review_required`, `fix_required`;
- `human_action`, `blocked`, `ready_to_merge`;
- `completed`, `idle`.

`implementation_route` accepts `github-direct`, `codex-fallback`, or `claude-fallback`.

`risk_tier` accepts `low`, `standard`, or `protected`.

`selected_auditor` accepts `none`, `codex`, or `claude`. `audit_state` accepts `not-required`, `required`, `pending`, `clean`, `blocked`, or `route-unavailable`.

Check conclusions accept `queued`, `in_progress`, `success`, `failure`, `cancelled`, `skipped`, `neutral`, `timed_out`, `action_required`, `stale`, or `missing`.

## Fail-closed relationships

The validator rejects ambiguous state, including:

- duplicate repositories;
- non-positive Issue or Pull Request numbers;
- non-lowercase or non-40-character SHAs;
- non-UTC timestamps;
- active PR/review states without an exact remote SHA;
- blocked, fix-required, or human-action states without a blocker;
- `human_action_required: true` outside `human_action` status;
- standard or protected work marked audit-not-required;
- ready-to-merge work with missing/failing checks or insufficient audit evidence;
- unknown fields, enum values, check names, or control characters.

Provider quota or setup failure may be represented as `audit_state: route-unavailable`. That state is automation-owned and does not itself justify `human_action_required: true`.

## Commands

Validate without writing:

```bash
python scripts/fleet_progress.py fleet.json --check
```

Render to stdout:

```bash
python scripts/fleet_progress.py fleet.json
```

Write only to an explicit path:

```bash
python scripts/fleet_progress.py fleet.json --output FLEET_STATUS.md
```

The renderer performs no network request, executes no external command, and does not inspect environment variables.

## Dashboard priority

Projects are grouped in this order:

1. Human Action Required;
2. Blocked or Route Unavailable;
3. Active Implementation and Review;
4. Ready to Merge;
5. Completed or Idle.

Rows are sorted deterministically by repository, Issue, and Pull Request. The Markdown shows a shortened SHA for readability; the JSON source retains the full exact SHA.

## Future protected phase

A later Issue may authorize read-only GitHub collection, GitHub Projects synchronization, scheduled refresh, durable dashboard publication, and Bootstrap propagation. Until then, the generated Markdown is only a projection; GitHub evidence remains authoritative.

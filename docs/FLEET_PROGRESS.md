# Fleet Progress Dashboard

The Fleet Progress Dashboard makes GitHub—not a chat transcript—the durable source of project progress. The offline renderer validates schema-version-1 JSON fail-closed and produces one deterministic cross-repository Markdown overview. A separately authorized read-only collector can populate exact Pull Request heads and GitHub Actions conclusions from live GitHub evidence.

Neither command mutates GitHub. GitHub Projects synchronization, scheduled refresh, durable dashboard publication, and Bootstrap propagation remain separate protected work.

## Status source

Use GitHub Issues, Pull Requests, exact remote SHAs, check conclusions, and audit evidence to construct the input. Do not treat a provider-reported local commit, a chat summary, or an unverified branch name as completion evidence.

The renderer input document has this form:

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

## Renderer commands

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

## Read-only GitHub collector

The collector accepts an explicit configuration that chooses the current Issue, Pull Request, required workflow names, risk tier, audit state, next action, and genuine human-action decision. It automates only evidence that GitHub can prove directly: current Pull Request state, Draft state, exact remote head SHA, exact-head Pull-Request-triggered Actions runs, and evidence timestamps.

A configured project has this form:

```json
{
  "repository": "owner/example",
  "phase": "Fleet collector",
  "issue": 157,
  "pull_request": 158,
  "required_workflows": ["CI", "Unit Tests"],
  "implementation_route": "github-direct",
  "risk_tier": "protected",
  "selected_auditor": "codex",
  "audit_state": "pending",
  "next_action": "Complete one exact-SHA audit",
  "blocker": null,
  "human_action_required": false,
  "baseline_status": null
}
```

Wrap project records in:

```json
{
  "schema_version": 1,
  "projects": []
}
```

For a project without a current Pull Request, `required_workflows` must be empty and `baseline_status` must be one of `backlog`, `ready`, `completed`, or `idle`. For a current Pull Request, at least one required workflow is mandatory and `baseline_status` must be null.

Validate collector configuration offline, with no network request or output mutation:

```bash
python scripts/fleet_collect_github.py fleet-source.json --check-config
```

Collect schema-version-1 JSON to stdout:

```bash
python scripts/fleet_collect_github.py fleet-source.json
```

Write only to an explicit path, then render it:

```bash
python scripts/fleet_collect_github.py fleet-source.json --output fleet.json
python scripts/fleet_progress.py fleet.json --output FLEET_STATUS.md
```

Authentication is optional for public repositories. Private-repository access may use `GH_TOKEN` or `GITHUB_TOKEN`. If both are set, they must contain the same value. The collector reads only those two fixed environment-variable names and never prints, hashes, persists, partially reveals, or echoes token values.

### Exact-head evidence rules

For every configured Pull Request, the collector:

1. reads the Pull Request through a fixed HTTPS GET request to `api.github.com`;
2. requires a same-repository Pull Request and a lowercase 40-character current `head.sha`;
3. queries only Pull-Request-triggered Actions runs filtered to that exact SHA;
4. rejects any returned run bound to another SHA or event;
5. selects the newest run for each required workflow by timestamp and run ID;
6. records `missing` when exact-head evidence for a required workflow is absent;
7. validates the generated document with the renderer contract before output.

The first response page is bounded to 100 exact-head workflow runs. An incomplete or oversized response fails closed instead of silently selecting partial evidence.

### Conservative status derivation

- merged Pull Request: `completed`;
- closed but unmerged Pull Request: `blocked`;
- explicit `human_action_required: true`: `human_action` with a required blocker;
- failing, cancelled, timed-out, or stale required workflow: `fix_required`;
- missing or `action_required` workflow evidence: `blocked` unless human action was separately and explicitly established;
- queued or in-progress required workflow: `ci_running`;
- successful workflows with required or pending audit: `review_required`;
- successful workflows with `route-unavailable` audit: `blocked` while `human_action_required` remains false;
- `ready_to_merge`: only an open non-Draft Pull Request with passing exact-head workflows and merge-ready audit evidence.

### Network and mutation boundary

The collector uses the Python standard library only. It can construct only HTTPS GET requests to fixed `/repos/{owner}/{repository}/pulls/{number}` and `/repos/{owner}/{repository}/actions/runs` endpoint families on `api.github.com`. Configuration cannot choose a host, API base, method, endpoint, request header, command, repository URL, or mutation.

It performs no POST, PUT, PATCH, DELETE, GraphQL operation, workflow dispatch, GitHub Project update, comment, label, branch, file, Issue, Pull Request, repository-setting, deployment, billing, or production mutation. HTTP errors are sanitized without printing response bodies or private URLs.

## Dashboard priority

Projects are grouped in this order:

1. Human Action Required;
2. Blocked or Route Unavailable;
3. Active Implementation and Review;
4. Ready to Merge;
5. Completed or Idle.

Rows are sorted deterministically by repository, Issue, and Pull Request. The Markdown shows a shortened SHA for readability; the JSON source retains the full exact SHA.

## Future protected phases

Later Issues may authorize GitHub Projects synchronization, scheduled refresh, durable dashboard publication, and Bootstrap propagation. Until those phases are reviewed and merged, generated Markdown remains a projection; GitHub Issues, Pull Requests, exact-head workflow evidence, and audit evidence remain authoritative.

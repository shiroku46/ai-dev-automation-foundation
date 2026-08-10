# Read-only authentication presence detection

Issue: #275

## Purpose

The authentication planner added by #273 can avoid unnecessary login requests only when it receives trustworthy non-secret capability signals. This detector supplies local CLI authentication presence as booleans without reading or retaining identity or credential output.

Official command behavior was rechecked on 2026-08-10.

## Supported checks

### GitHub CLI

Command:

```text
gh auth status --active --hostname github.com
```

GitHub documents `gh auth status` as the command for testing authentication state. The detector never uses `--show-token` and never calls `gh auth token`.

Reference: <https://cli.github.com/manual/gh_auth_status>

### Vercel CLI

Command:

```text
vercel whoami
```

Vercel documents `vercel whoami` as displaying the user currently logged into Vercel CLI. The detector discards that output and uses only the process exit status.

Reference: <https://vercel.com/docs/cli/whoami>

### Cloudflare Wrangler

Command:

```text
wrangler whoami --json
```

Cloudflare documents `wrangler whoami --json` as returning user information and exiting non-zero when authentication is unavailable. The detector discards the JSON entirely. It never invokes `wrangler auth token`.

Reference: <https://developers.cloudflare.com/workers/wrangler/commands/general/>

## Execution boundary

The detector:

- resolves only the fixed executable names `gh`, `vercel`, and `wrangler` from `PATH`;
- invokes commands as argument arrays with `shell=False`;
- sends stdin, stdout, and stderr to `DEVNULL`;
- uses a bounded timeout;
- returns only `provider`, `executable_present`, and `authenticated`;
- treats missing executables, non-zero exits, timeouts, and OS execution failures as boolean negative results;
- does not parse provider output or return error text.

This intentionally means the detector cannot tell the caller which account is logged in, which scopes a token has, where credentials are stored, or what credential type is being used. Those details are outside this layer.

## Planner integration

For GitHub, the planner preference is:

1. connected GitHub App/API route;
2. existing authenticated GitHub CLI session;
3. one-time interactive GitHub App connection.

Vercel and Cloudflare already accept CLI/OAuth authentication booleans in the planner. A coordinator may map successful detector results into those existing capability fields without transferring any provider command output.

## Security invariants

The following commands or flags are outside the detector boundary because they can retrieve or accept credential values:

- `gh auth token`;
- `gh auth status --show-token`;
- Vercel `--token` authentication;
- `wrangler auth token`.

The detector also does not perform login, logout, refresh, token creation, Secret mutation, account selection, deployment, repository mutation, or workflow mutation.

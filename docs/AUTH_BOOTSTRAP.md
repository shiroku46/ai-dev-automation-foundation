# Authentication Bootstrap Planner

Issue: #273

## Purpose

Foundation should remove recurring credential work where the platform already provides a safer automatic or short-lived authentication path. This first increment is deliberately read-only: it plans the next authentication action from explicit non-secret capability flags and never reads or handles credential values.

The planner has three states:

- `automatic` — an already connected or credentialless/short-lived route can be used without a new human step.
- `interactive_once` — one interactive account connection/login is still required, after which the existing session/integration can be reused.
- `manual_required` — the platform's supported route still requires a human-created credential prerequisite.

`human_action_required` is true only for the latter two states.

## Current platform boundary

Verified against official documentation on 2026-08-10.

### GitHub

GitHub Actions can request OIDC identity tokens and exchange them for short-lived credentials with providers that support federation. This avoids storing long-lived cloud credentials in GitHub Secrets. Foundation therefore prefers a connected GitHub App/API route and treats initial app connection as a one-time interactive step.

Reference: <https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-cloud-providers>

### Vercel

Vercel supports OIDC federation and issues short-lived OIDC tokens in builds/functions for supported backend federation scenarios. When a Vercel Git integration already owns deployment, Foundation should prefer that integration over adding a personal deployment token. An existing OIDC-capable or authenticated CLI route may also avoid new credential creation.

Reference: <https://vercel.com/docs/oidc>

### Cloudflare

Wrangler supports interactive OAuth login for local use. Wrangler also provides `wrangler auth token`, but that command returns active credential material and is explicitly outside the Foundation planner boundary.

For GitHub Actions deployment of Workers, Cloudflare's current official documentation requires a scoped Cloudflare API token plus account ID. Until Cloudflare documents a supported credentialless CI route, missing CI credentials remain a one-time `manual_required` prerequisite rather than something Foundation pretends it can generate safely.

References:

- <https://developers.cloudflare.com/changelog/post/2025-12-18-wrangler-auth-token/>
- <https://developers.cloudflare.com/workers/ci-cd/external-cicd/github-actions/>

## CLI

The planner accepts only explicit capability booleans supplied by the caller. It does not auto-detect by reading environment values and it does not run provider CLIs.

Examples:

```bash
python scripts/auth_bootstrap.py github \
  --capabilities-json '{"github_app_connected":true}'

python scripts/auth_bootstrap.py vercel \
  --capabilities-json '{"git_integration_connected":true}'

python scripts/auth_bootstrap.py cloudflare --route local \
  --capabilities-json '{"wrangler_oauth_authenticated":false}'

python scripts/auth_bootstrap.py cloudflare --route github_actions \
  --capabilities-json '{"api_token_configured":false,"account_id_configured":false}'
```

Output is one deterministic JSON object. It contains only schema identity, provider/route, state, a human-action boolean, an action code, and a rationale code.

## Security invariants

The planner must not:

- read credential values from environment variables, files, provider command output, stdin, or network responses;
- invoke `wrangler auth token` or any equivalent credential-retrieval command;
- execute login flows or subprocesses;
- print, copy, hash, persist, compare, infer, or validate secret values;
- expose account IDs, emails, filesystem paths, command output, or environment values;
- mutate GitHub, Vercel, Cloudflare, repository settings, Secrets, or workflow files.

Capability flags are assertions supplied by a trusted caller. Later increments may add safe detectors, but any detector must prove authentication presence without returning credential material.

## Next increment

A later opt-in implementation may add provider-specific presence detectors and setup executors. Each executor must remain separated from the planner, preserve the no-secret-value boundary, and stop only when a provider genuinely requires a human account-authorized action.

# Authentication Bootstrap Planner

Issues: #273, #281

## Purpose

Foundation should remove recurring credential work where the platform already provides a safer automatic, managed, or short-lived authentication path. The planner is deliberately non-secret: it plans the next authentication action from explicit capability flags and never reads or handles credential values.

The planner has three states:

- `automatic` — an already connected, managed, or short-lived route can be used without a new human step.
- `interactive_once` — one interactive account or Git integration connection is still required, after which the existing session/integration can be reused.
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

For ordinary Worker deployment from GitHub or GitLab, Foundation prefers Cloudflare Workers Builds Git integration. Cloudflare documents Workers Builds as the minimal-setup CI/CD route for Git-connected repositories. After the Git integration is authorized, Cloudflare automatically generates an account API token for Builds by default and keeps using that token. The operator therefore does not need to create and store a Cloudflare deployment token in GitHub Actions for this preferred route.

The first Workers Builds Git integration still requires interactive authorization of the Cloudflare Git integration. Foundation represents that as `interactive_once`; it does not automate the dashboard or pretend the Builds REST API removes the initial bootstrap boundary.

External GitHub Actions remains an explicit fallback. Cloudflare's current official GitHub Actions documentation still requires a scoped Cloudflare API token plus account ID, so missing credentials on the explicit `github_actions` route remain `manual_required`.

References:

- <https://developers.cloudflare.com/changelog/post/2025-12-18-wrangler-auth-token/>
- <https://developers.cloudflare.com/workers/ci-cd/builds/>
- <https://developers.cloudflare.com/workers/ci-cd/builds/git-integration/>
- <https://developers.cloudflare.com/workers/ci-cd/builds/configuration/>
- <https://developers.cloudflare.com/workers/ci-cd/external-cicd/github-actions/>

## Cloudflare route policy

- `deployment` — preferred Git-connected deployment route. Uses Workers Builds when already connected; otherwise requests one interactive Git integration authorization.
- `local` — local Wrangler OAuth session detection/setup.
- `github_actions` — explicit external-CI fallback. Existing configured credentials are reused; missing token/account prerequisites remain manual.

The `deployment` route never falls back silently to user-created GitHub Actions credentials.

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

python scripts/auth_bootstrap.py cloudflare --route deployment \
  --capabilities-json '{"workers_builds_git_connected":false}'

python scripts/auth_bootstrap.py cloudflare --route github_actions \
  --capabilities-json '{"api_token_configured":false,"account_id_configured":false}'
```

Output is one deterministic JSON object. It contains only schema identity, provider/route, state, a human-action boolean, an action code, and a rationale code.

## Security invariants

The planner must not:

- read credential values from environment variables, files, provider command output, stdin, or network responses;
- invoke `wrangler auth token` or any equivalent credential-retrieval command;
- create Cloudflare API tokens or call Workers Builds APIs;
- execute login flows or subprocesses;
- automate provider dashboard/browser authorization;
- print, copy, hash, persist, compare, infer, or validate secret values;
- expose account IDs, emails, filesystem paths, command output, or environment values;
- mutate GitHub, Vercel, Cloudflare, repository settings, Secrets, or workflow files.

Capability flags are assertions supplied by a trusted caller. Safe detectors and setup executors remain separate from this planner and preserve the no-secret-value boundary.

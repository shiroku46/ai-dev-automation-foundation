# Guarded one-time interactive authentication setup

Issue: #277

## Purpose

`auth_setup.py` composes the accepted read-only authentication detector with narrowly fixed provider login commands. Its goal is to make authentication setup idempotent: do nothing when authentication is already usable, request interaction only when it is missing, and never route credential values through Foundation.

Official provider behavior was rechecked on 2026-08-10.

## Default behavior

The command is planning-only unless `--interactive` is explicitly supplied.

```text
python scripts/auth_setup.py github
python scripts/auth_setup.py vercel
python scripts/auth_setup.py cloudflare
```

Possible non-secret statuses include:

- `ready` — existing authentication is usable;
- `install_required` — the provider CLI is absent;
- `interaction_required` — authentication is missing and an interactive login can be started;
- `interactive_terminal_required` — `--interactive` was requested outside a real terminal;
- `login_failed` / `login_timeout` — the provider login command did not complete successfully;
- `verification_failed` — the login command exited successfully but the read-only detector still cannot confirm authentication.

Foundation does not auto-install provider software.

## Interactive mode

```text
python scripts/auth_setup.py github --interactive
python scripts/auth_setup.py vercel --interactive
python scripts/auth_setup.py cloudflare --interactive
```

The executor requires a TTY, uses `shell=False`, inherits terminal input/output directly, and never captures provider login output. After the provider command exits successfully, Foundation runs the read-only detector again before reporting `ready`.

### GitHub

Fixed command:

```text
gh auth login --hostname github.com --web --git-protocol https --skip-ssh-key
```

GitHub CLI documents a browser-based authentication flow. `--skip-ssh-key` prevents the SSH-key creation/upload prompt that can occur when SSH is selected. Foundation selects HTTPS and does not use `--with-token` or `--insecure-storage`.

GitHub CLI normally stores the resulting authentication token in the system credential store, but its documentation states that it can fall back to plaintext storage if no credential store is available. Foundation therefore makes no claim about GitHub CLI credential-at-rest storage and does not inspect the stored credential or its location.

Reference: <https://cli.github.com/manual/gh_auth_login>

### Vercel

Fixed command:

```text
vercel login
```

Vercel's current CLI login uses OAuth 2.0 Device Flow. The executor does not use Vercel's `--token` option and does not use deprecated provider-specific login selectors.

References:

- <https://vercel.com/docs/cli/login>
- <https://vercel.com/changelog/new-vercel-cli-login-flow>

### Cloudflare

Fixed command:

```text
wrangler login --use-keyring
```

Wrangler's current `login` command uses OAuth. Cloudflare documents that plain `wrangler login` stores OAuth credentials in plaintext TOML by default; `--use-keyring` opts into OS-keychain-backed credential protection. Foundation therefore fixes `--use-keyring` in the only permitted Cloudflare local login command.

The executor never invokes `wrangler auth token`, which returns active credential material.

Reference: <https://developers.cloudflare.com/workers/wrangler/commands/general/>

## Security boundary

The setup executor does not:

- accept a token/API key/PAT from stdin, arguments, environment variables, or files;
- retrieve or display credential values;
- capture provider login output;
- parse OAuth URLs, device codes, usernames, emails, account IDs, credential paths, or provider error text;
- execute through a shell;
- install `gh`, Vercel CLI, Wrangler, package managers, browser helpers, or keyring dependencies;
- run interactive login in CI/headless mode;
- create Cloudflare CI API tokens;
- mutate repository settings, GitHub Secrets, workflows, or deployments.

Provider login processes necessarily receive and store credentials according to their own official implementation. The Foundation boundary is that those values are never transferred into Foundation's own output, state, logs, Issue/PR evidence, or planner inputs.

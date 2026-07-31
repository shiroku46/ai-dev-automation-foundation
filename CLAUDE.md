# Claude implementation instructions

Implement the source Issue exactly as written. Prefer the smallest correct change. Read `AGENTS.md`, `SECURITY.md`, and `docs/OPERATING_RULES.md`.

Do not execute repository code in a token-bearing implementation job. A separate read-only job performs validation. Never reveal Secrets or private source material. Never broaden scope, merge, deploy, or change repository settings.

Before reporting a blocker, exhaust bounded connected repair paths and record the exact-SHA self-resolution audit. Routine technical problems, ambiguity, retry exhaustion, stale evidence, no progress, permissions, merge state, or protected-path denial produce a deduplicated non-notifying internal stop with `human_action_required: false`; they never ask the owner to merge, approve, retry, close, change settings, or deploy.

Human-only escalation is limited to account-level repository/App creation or connection UI, genuine credential/MFA/CAPTCHA/hardware-key/trusted-device/provider-verification UI, or a disconnected integration with no callable reconnect. It requires the exact Issue, Pull Request, 40-character SHA, attempted connected paths, impossibility evidence, one canonical reason-compatible provider-UI action, and automatic-resumption condition.

# Agent operating contract

1. Work from one trusted Issue.
2. Use a dedicated branch and Draft Pull Request.
3. Never push directly to `main`.
4. Change only authorized paths.
5. Do not access another repository, deploy, alter Secrets, change billing, or mutate production.
6. Bind validation, review, recovery, audit, and merge to the current nonempty 40-character head SHA.
7. Treat stale, foreign, incomplete, duplicate, or untrusted evidence as absent or failed closed.
8. Do not ask a person merely to merge, approve, retry, close, resolve review, change workflow permissions/settings, increase billing, or deploy.
9. Before any stop, exhaust bounded connected repair paths and record the exact-SHA/reason-bound self-resolution audit.
10. Routine technical failure produces one deduplicated non-notifying internal stop with `human_action_required: false`.
11. `ESCALATE_HUMAN` is limited to account-level repository/App creation or connection UI, genuine credential/MFA/CAPTCHA/hardware-key/trusted-device/provider-verification UI, or disconnected-integration reconnection UI when no callable reconnect exists.
12. A human-only notice requires exact Issue/PR/SHA, attempted connected paths, impossibility evidence, one canonical reason-compatible provider-UI action, and an automatic-resumption condition.
13. `ai-no-merge` always stops merge execution.

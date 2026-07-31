# Deterministic recovery supervisor

`scripts/ai_recovery_supervisor.py` is a pure decision engine. It receives immutable JSON-like state and returns one action, one reason code, an audit explanation, and an idempotency key.

The engine:

- rejects stale check, review, and bounded-fix evidence;
- requires a complete trusted check manifest;
- applies cooldowns and finite retry budgets;
- distinguishes transient failure, deterministic failure, review blocker, missing evidence, unauthorized protected scope, and human-only risk;
- canonicalizes semantically unordered collections before hashing;
- binds bounded-fix evidence to exact SHA, run identity, and failure fingerprint;
- never invents a next phase;
- treats Secret, permission, billing, authentication, repository-setting, deployment, production, destructive-data, and essential ambiguity as human-only.

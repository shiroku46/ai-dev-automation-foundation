# AI Development Automation Foundation

Reusable, security-focused GitHub automation for unattended software development.

## Goals

- turn scoped GitHub Issues into reviewed Pull Requests;
- bind validation and review evidence to the exact current commit SHA;
- diagnose and recover from routine failures automatically;
- merge eligible Pull Requests without requiring a human merge click;
- require human action only for genuinely unavailable credentials, identity verification, or disconnected capabilities;
- remain safe for public repositories and untrusted forks.

## Security model

- no direct pushes to `main` after initial repository seeding;
- no proposed-branch code is executed in write-capable jobs;
- untrusted contributions receive read-only validation only;
- write-capable automation is controlled by the default branch;
- required checks and independent review must refer to the exact current SHA;
- merges use an expected-head-SHA guard;
- secrets are never printed, transferred, hashed, or committed;
- retries and repairs are bounded, idempotent, and auditable.

## Repository status

This public repository is being populated from a sanitized, history-free allowlist of reusable automation components. Product-specific plans, private workspace references, credential diagnostics, billing data, and private repository history are intentionally excluded.

The disposable acceptance repository is `shiroku46/ai-dev-automation-foundation-e2e`.

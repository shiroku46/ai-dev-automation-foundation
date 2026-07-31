# Security Policy

## Reporting

Do not publish credentials, tokens, private keys, billing information, private logs, or personal data in an Issue or Pull Request.

Report a suspected vulnerability privately to the repository owner through an appropriate private GitHub contact channel.

## Public automation boundary

- Forked and external Pull Requests must receive read-only validation only.
- Workflows using write permissions, OIDC, or repository mutation must use default-branch-controlled code and must not check out or execute proposed-branch code.
- Issue and comment commands must validate the authorized actor and exact standalone command syntax.
- Pull Request eligibility and recovery decisions must use the exact current head SHA and reject stale evidence.
- Workflow names, repositories, refs, shell commands, and privileged operations must not be selected from untrusted Issue or Pull Request text.
- Secrets must not be printed, persisted, transferred between jobs, hashed for comparison, or committed.

## Supported state

Only the current default branch is supported. Security fixes are handled through a scoped Issue, dedicated branch, exact-SHA validation, independent review, and a guarded Pull Request merge.

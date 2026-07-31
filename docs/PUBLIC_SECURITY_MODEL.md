# Public workflow security model

| Context | Code source | Permissions | Secrets |
|---|---|---:|---:|
| Fork or same-repo PR checks | exact PR head | `contents: read` | none |
| Queue implementation | default workflow plus Claude write step | bounded repository write | one configured Claude credential |
| Reconciliation | default branch | fixed Actions/status writes | none |
| Supervisor | default branch | bounded issue/PR/status/action writes | none |

Write-capable jobs never check out a proposed branch. They fetch metadata through the GitHub API and dispatch fixed workflows against an immutable SHA.

Actions are pinned to immutable commit SHAs. Candidate scans are bounded. Same-repository head provenance is mandatory. Idempotency markers prevent duplicate comments and repeated actions.

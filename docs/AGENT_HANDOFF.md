# Minimal exact-SHA agent handoff bundle

## Purpose

A handoff bundle lets a new coding-agent session recover task state from GitHub-visible artifacts instead of depending on hidden chat history. It is an opt-in experiment until the controlled evaluation suite proves that it improves interruption recovery without increasing defects, false human requests, or operating cost.

The first bundle intentionally contains only:

- `.ai-dev/task-state.json` — current machine-readable task state;
- `.ai-dev/decisions.jsonl` — canonical append-only decision history;
- `.ai-dev/handoff.md` — a bounded human-readable recovery note.

The Foundation contract does not automatically create or commit these files in product repositories. A later Issue must separately authorize any runtime integration or Bootstrap distribution.

## Authoritative parser

`scripts/agent_handoff_contract.py` is the authoritative standard-library parser. `docs/AGENT_HANDOFF.schema.json` describes the structural JSON contracts. The parser additionally enforces exact byte digests, canonical JSONL, stale-head rejection, Markdown consistency, secret-pattern rejection, and cross-field rules.

## Deterministic writer order

Writers produce a bundle in this order:

1. Write every decision as one canonical UTF-8 JSON object using sorted keys and compact separators. End every record, including the final record, with LF. An empty decision history is the zero-byte file.
2. Write `.ai-dev/handoff.md` from the selected repository, Issue, candidate SHA, current status, next automatic action, and blocker list. Use LF and end with a final newline.
3. Compute SHA-256 over the exact decision and Markdown bytes.
4. Write `.ai-dev/task-state.json` last, recording both digests and the exact decision count.

Any later byte change to the decision history or Markdown requires regenerating the state file. The state file is never trusted merely because it parses.

## Live identity and stale-state boundary

A consumer must supply the live expected repository, Issue number, base SHA, and candidate SHA. The parser rejects the bundle when any of those values differs from `task-state.json`. The Markdown marker must also carry the same repository, Issue, and candidate SHA.

Historical decision records retain the exact head SHA at which each decision was recorded. They are not themselves completion evidence. The current candidate identity comes only from the live recheck and current task state.

A moved PR head invalidates the prior task state and handoff document. Do not copy a stale next action into a new session and do not treat historical checks or provider output as current evidence.

## Task state

The state records only bounded recovery information:

- task, repository, Issue, base, candidate, and update identities;
- phase;
- completed and pending work;
- read and changed exact repository paths;
- next automatic action;
- bounded blockers;
- the audited human-action flag;
- decision count and exact artifact digests.

Work lists and paths are unique. Completed and pending work cannot overlap. Paths are exact POSIX-style repository-relative paths. Parent traversal, backslashes, empty segments, Windows drive paths, `.git` segments, absolute paths, and glob metacharacters are rejected; scope patterns such as `tests/**` are not handoff evidence.

A completed state has no pending work or blockers, never requires a person, and uses `next_action: "none"`. Any other phase must state a concrete next automatic action.

## Blockers and the human-only boundary

Technical blockers are automation-owned. If blockers exist, the phase is `blocked`; a blocked phase without blocker evidence is invalid.

`human_action_required: true` is accepted only when exactly one blocker uses one of the existing audited Foundation reason codes:

- `HUMAN_ONLY_ACCOUNT_LEVEL_REPOSITORY_CREATION_UI_UNAVAILABLE`
- `HUMAN_ONLY_CREDENTIAL_PROVIDER_UI_REQUIRED`
- `HUMAN_ONLY_DISCONNECTED_INTEGRATION_RECONNECTION_UI_REQUIRED`

Provider quota, missing review evidence, failing tests, merge state, path denial, collisions, stale SHA, and ambiguous technical conditions remain non-human blockers. This handoff contract does not create a new notification route and cannot prove that a UI-only condition exists; the live runtime must still perform its reason-specific connected audit.

## Decision history

Each JSONL record is bound to the same repository and Issue and includes:

- a unique deterministic decision ID;
- the exact head SHA at which it was recorded;
- a UTC timestamp;
- bounded summary and rationale;
- an optional prior decision ID that it supersedes.

Records are append ordered by timestamp. A replacement may supersede only a currently active earlier decision. The parser rejects unknown targets and ambiguous replacement branches. Existing lines are never edited to mark them inactive; the active decision set is derived from the append-only chain.

## Handoff Markdown

The document begins with this exact metadata shape:

```text
# Agent handoff

<!-- foundation-agent-handoff
schema_version: 1
repository: OWNER/REPOSITORY
issue_number: 123
candidate_sha: 0000000000000000000000000000000000000000
-->
```

It then contains exactly one of each section, in order:

- `## Current status`
- `## Next automatic action`
- `## Technical blockers`

The next-action section must exactly match `task-state.json`. The blocker section is `None.` or the canonical `- CODE: detail` lines in state order. The Markdown digest in task state covers every byte.

## Data boundary

The bundle stores no credential or Secret value, private key, raw hidden reasoning, chain-of-thought transcript, production payload, personal profile, or unbounded log. The parser rejects several high-confidence token, private-key, credential-assignment, and hidden-reasoning markers, but that scan is not a complete personal-data classifier. Producers remain responsible for recording only concise operational facts and decisions.

## Evaluation and adoption

Phase B is not accepted as the default workflow merely because the parser exists. Issue #216 must compare interrupted tasks with and without the bundle using the accepted evaluation-run contract. Measure at least:

- successful recovery on the correct SHA;
- stale-state rejection;
- repeated failed attempts;
- iterations and elapsed time;
- false human-action requests;
- regressions and task success;
- additional API, Actions, and provider cost.

If the measured benefit is absent or smaller than trial and environment uncertainty, retain the simpler GitHub-direct baseline.

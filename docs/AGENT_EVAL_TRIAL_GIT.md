# Deterministic Git identity for isolated evaluation trials

## Purpose

The `AGENT_EVAL_RUN` and grader contracts use exact 40-character Git SHAs, while Phase D agent workspaces intentionally contain only synthetic fixture bytes and no Foundation `.git` directory. `scripts/agent_eval_trial_git.py` supplies a real local Git identity without weakening that isolation boundary.

It executes only credential-free local `git` commands. It never configures a remote, fetches or pushes, invokes an agent/provider/grader, or places Git metadata inside the agent-visible workspace.

## Layout

A trial has two disjoint local paths:

- the agent-visible fixture workspace;
- a bare SHA-1 Git metadata directory created outside that workspace.

The metadata directory carries Git objects, refs, and index state. The workspace never receives `.git`. The controller should not expose the metadata path or Git environment variables to the candidate unless a later accepted executor contract explicitly requires it.

## Deterministic baseline

`initialize_trial_git_identity(request, workspace, metadata_dir)` first inspects the workspace through the accepted delta contract. Baseline initialization is allowed only when the current workspace exactly matches the sealed fixture and has zero mutations.

The local Git repository is initialized as bare SHA-1 with no remote. System and global Git configuration are disabled. All fixture files are force-staged so ignore rules cannot omit a sealed file. A root commit is created with fixed internal author/committer identity, fixed message, and fixed UTC timestamp, then stored at `refs/heads/baseline`.

Because the commit contains only the deterministic fixture tree plus fixed metadata, identical sealed fixture bytes produce the same baseline SHA across temporary directories and trials.

## Deterministic candidate

After the candidate process has stopped, `finalize_trial_git_identity`:

1. revalidates request and initialization evidence;
2. verifies the baseline ref and tree have not moved;
3. confirms the bare metadata repository still has no remote;
4. safely inspects the current candidate workspace through the delta contract;
5. force-stages the entire current state, including additions, deletions, renames, and mode changes;
6. creates one fixed-metadata child commit whose only parent is the baseline;
7. stores it at `refs/heads/candidate`;
8. verifies candidate parent and tree identity;
9. re-inspects the workspace and requires byte/mode-identical delta evidence to the pre-commit inspection.

An unchanged candidate still receives a distinct child commit. Its tree equals the baseline tree, while the candidate commit SHA remains a real exact Git identity for that evaluated trial state.

## Local Git isolation

Git is resolved to an absolute executable. Commands use bounded time/output and a small runtime environment. System/global Git configuration is disabled, author/committer fields are fixed internal values, and no repository credential variables are copied intentionally.

The module never creates a remote and verifies the remote list is empty during initialization and finalization. It rejects metadata/workspace nesting, pre-existing metadata destinations, `.git` metadata path components, unsafe or mutated workspaces, moved refs, and unavailable Git.

## Evidence

Initialization evidence contains:

- sealed request SHA256;
- workspace and metadata paths;
- baseline commit and tree SHA;
- sealed baseline bundle SHA256.

Final evidence contains:

- sealed request SHA256;
- workspace and metadata paths;
- base and candidate commit SHAs;
- candidate tree SHA;
- candidate bundle SHA256;
- mutation and scope-violation counts.

The final base/candidate SHAs can be supplied to the trusted grader expectation and run controller. They are identity evidence only; task success still depends on delta, grader, human-boundary, handoff, and required-check evidence.

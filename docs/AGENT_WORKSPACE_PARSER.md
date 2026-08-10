# Strict isolated-workspace capability parser

Issue: #287
Depends on: #285 / PR #286

## Purpose

The F1 capability schema describes what an isolated coding-agent workspace adapter must prove. `scripts/agent_workspace_contract.py` turns one such declaration into immutable Foundation-side evidence before any provider-specific adapter may be selected or invoked.

The parser is deliberately offline and provider-neutral. It does not inspect a host, provider account, sandbox, filesystem, environment, Git repository, credential store, network endpoint, or the JSON-Schema file at runtime. Provider-specific adapter tests are still responsible for proving that a declaration matches real adapter behavior.

## Input boundary

`parse_workspace_capability(content: bytes)` accepts exactly one bounded canonical UTF-8 JSON object.

It rejects:

- empty or oversized input;
- UTF-8 BOM or invalid UTF-8;
- duplicate JSON keys;
- `NaN`, `Infinity`, or `-Infinity`;
- trailing JSON or trailing whitespace/newlines;
- non-canonical pretty/key ordering;
- unknown or missing fields at any object boundary.

Canonical input is compact UTF-8 JSON with sorted object keys, no insignificant whitespace, and already sorted/unique capability arrays. The parser does not silently normalize an ambiguous declaration and then accept it.

## Immutable evidence

A valid declaration becomes frozen dataclasses for:

- adapter identity;
- isolation primitive;
- workspace boundary;
- execution capability;
- network capability;
- credential capability;
- lifecycle capability;
- security capability;
- top-level manifest evidence.

Capability arrays become immutable tuples. The top-level evidence also carries `manifest_sha256`, calculated over the canonical F1 manifest bytes without adding the digest to the manifest itself.

`serialize_workspace_capability` reconstructs and revalidates the complete semantic payload before returning canonical bytes. Tampering with nested evidence or the stored digest fails closed.

## F1 semantic enforcement

The parser independently enforces the accepted F1 contract rather than depending on an optional JSON-Schema runtime package.

Required Foundation-compatible properties include:

- schema version is the integer `1`; JSON boolean `true` is not accepted as an integer;
- stable bounded adapter ID and provider label;
- exact lowercase 40-character adapter revision SHA;
- known descriptive isolation, persistence, command, network, credential, and cleanup values;
- absolute bounded Linux-style workspace root without parent traversal, backslashes, drive prefixes, NUL, or control characters;
- host filesystem not visible to the agent workspace;
- Git metadata visibility exactly `external`;
- execution timeout from 1 through 3600 seconds, never a boolean or unbounded value;
- sorted unique network modes including `deny-all`;
- sorted unique credential and lifecycle capability sets;
- cleanup that the trusted controller can enforce;
- host credentials, repository-write credentials, Secrets, OIDC, and provider control-plane mutation all unavailable inside the agent workspace.

The parser preserves descriptive values such as `microvm` versus `vm-isolated-container` and `argv` versus `shell-string`; it does not rank providers or infer capabilities that were not declared and later independently proven.

## No runtime side channel

The parser imports only Python standard-library modules needed for deterministic data validation and hashing. It has no subprocess, socket, HTTP, provider SDK, environment-variable, filesystem, credential, Secret, OIDC-token, account-state, billing, or sandbox-control interface.

This separation matters because a capability parser must not become a hidden provider login or host-introspection path. Provider/controller authentication belongs outside the agent workspace and outside this parser.

## Adapter handoff

A later provider-specific adapter may carry canonical manifest bytes in its trusted implementation package and parse them through this contract. That parser success proves only that the declaration is internally valid and meets Foundation's minimum capability shape. It does **not** prove the provider actually enforces the declaration.

Concrete adapter acceptance therefore needs separate evidence that the exact adapter revision can enforce the claimed filesystem, Git, command, network, credential, lifecycle, and security properties. If runtime evidence and declared capabilities diverge, the adapter fails closed; the manifest is never treated as self-authorization.

## Adoption boundary

F2 changes no candidate execution route and makes no Vercel, Cloudflare, or other provider recommendation. Phase F adapters remain experimental until controlled execution evidence satisfies the Foundation Next adoption gates.

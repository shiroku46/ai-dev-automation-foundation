# Provider-neutral isolated workspace capability contract

Issue: #285
Parent Epic: #213

## Purpose

Foundation must compare isolated workspace adapters against one machine-readable contract before any adapter is trusted to execute candidate code. A provider product name is descriptive metadata only; it is never evidence that a particular isolation, network, credential, cleanup, or Git boundary is available.

`AGENT_WORKSPACE_CAPABILITY.schema.json` defines the minimum capability manifest accepted by Foundation. F1 defines the evidence shape only. It creates no sandbox, provider connection, account resource, SDK dependency, network request, credential, deployment, or runtime route.

## Current platform evidence

Platform behavior was rechecked against official documentation on 2026-08-10.

### Vercel Sandbox

Vercel documents Sandbox as an ephemeral isolated Firecracker microVM with its own filesystem and network. Its current product documentation also exposes network firewall policies including deny-all/allowlist behavior and credential brokering that injects credentials into outbound requests without putting the credential inside the sandbox. Vercel documents project-associated OIDC as the recommended Sandbox authentication route and automatic when production code runs on Vercel.

References:

- <https://vercel.com/docs/sandbox>
- <https://vercel.com/sandbox>

The Foundation manifest records this isolation primitive as `microvm`; that label does not rank it above or below another accepted primitive.

### Cloudflare Sandbox SDK

Cloudflare documents Sandbox SDK as isolated execution built on Containers, with each sandbox running in a separate VM. It supports command/file operations, VM-level filesystem/process/network isolation, public-internet disablement through `enableInternet = false`, host allowlists/outbound handlers, and Worker-side outbound/proxy credential injection so the real credential can remain outside sandbox code.

References:

- <https://developers.cloudflare.com/sandbox/concepts/security/>
- <https://developers.cloudflare.com/sandbox/guides/outbound-traffic/>
- <https://developers.cloudflare.com/sandbox/guides/proxy-requests/>

The Foundation manifest records this descriptive primitive as `vm-isolated-container`; it is not treated as identical to a Firecracker microVM.

Cloudflare also published a June 2026 Sandbox SDK migration guide for deprecated transport/session APIs. A future Cloudflare adapter must verify current supported APIs at implementation time instead of copying older Sandbox examples.

Reference: <https://developers.cloudflare.com/sandbox/guides/2026-deprecation/>

## Manifest identity

Every manifest contains an `adapter_id`, provider label, and exact lowercase 40-character `revision_sha`. The revision is the adapter implementation identity to which the capability claim applies. Provider identity never grants authorization and a manifest must be regenerated/revalidated when adapter behavior changes.

Manifests are serialized as normal JSON but Foundation consumers must treat a canonical representation as sorted-key, compact UTF-8 JSON. Arrays representing capability sets must already be sorted and unique so logically equivalent manifests have one stable representation.

## Isolation and filesystem

`isolation.primitive` is descriptive and bounded:

- `microvm`
- `vm-isolated-container`
- `container`
- `process-isolated`

F1 does not declare these security-equivalent. A later adapter is accepted only for properties independently proven by its adapter tests.

Foundation-compatible workspace profiles require:

- `host_filesystem_visible: false`;
- `git_metadata_visibility: external`;
- a bounded absolute Linux workspace root;
- a declared persistence mode (`ephemeral`, `session-bounded`, or `persistent`).

External Git metadata preserves the existing Phase D/E rule that candidate code cannot read or mutate the trusted Git control boundary.

## Command execution

An adapter declares `command_transport` as either:

- `argv` — the adapter exposes command and argument boundaries separately; or
- `shell-string` — the adapter accepts one command string.

`argv` is not a blanket safety guarantee. `shell-string` is not forbidden, but trusted adapter code must construct it without interpolating untrusted fragments into controller-generated command text. Candidate code may still intentionally execute a shell inside its isolated workspace; the restriction concerns trusted-controller command construction.

`max_execution_seconds` is always finite and capped by the contract. A provider's larger platform maximum does not enlarge Foundation's accepted bound automatically.

## Network modes

The capability set is bounded to:

- `deny-all`
- `allowlist`
- `unrestricted`

Every Foundation-compatible adapter must prove `deny-all`. Candidate execution does not infer permission for unrestricted egress merely because an adapter supports it. `unrestricted` is available only to a separately trusted/authorized setup phase whose Issue explicitly requires it; it is not the Foundation candidate-execution default.

Public ports, preview URLs, tunnels, custom domains, browser sessions, and ingress authentication are outside F1. Their existence cannot be inferred from a network capability manifest.

## Credential modes

The only accepted capability declarations are:

- `none` — no external credential service is required for the isolated workload;
- `brokered-egress` — a trusted boundary outside the agent workspace adds an upstream credential to an allowed outbound request.

Brokered credentials must remain unavailable to sandbox filesystem, process environment, stdout/stderr, agent-visible request/context, and candidate code. Provider/account authentication used by the controller is a separate control-plane concern and does not imply that OIDC or another credential may enter the agent workspace.

## Security invariants

A Foundation-compatible manifest requires all of these to be false inside the agent workspace:

- host credentials visible;
- repository write credentials visible;
- Secrets visible;
- OIDC visible;
- provider control-plane mutation available.

These booleans describe the agent boundary, not the trusted orchestrator. A provider adapter may need trusted controller credentials or OIDC outside the workspace, provided later adapter evidence proves that candidate code cannot obtain them.

## Lifecycle and cleanup

Adapters declare one or more sorted unique cleanup capabilities:

- `destroy`
- `reset`
- `stop`

At least one declared cleanup operation must be controller-enforceable. F1 does not assume identical persistence semantics for these verbs; the provider-specific adapter must prove what state survives each lifecycle transition and choose a cleanup operation that returns the trial boundary to its accepted state.

## Fail-closed adapter acceptance

A manifest is insufficient by itself. Future F2/F3 provider adapters must test the declared properties against the exact adapter revision. Foundation rejects a profile when any required property is unknown, unsupported, stale, widened, or cannot be independently demonstrated.

In particular, rejection is required when:

- deny-all egress cannot be enforced;
- Git metadata must be inside the agent workspace;
- host/repository credentials, Secrets, or OIDC are visible to candidate code;
- candidate code can mutate the provider control plane;
- execution time is unbounded;
- cleanup is not controller-enforceable;
- capability arrays are unsorted or duplicated;
- the adapter revision cannot be tied to exact code.

## Human-action and billing boundary

F1 does not install or authorize a provider and does not infer account eligibility. Paid-plan requirements, provider account creation, billing, OAuth/App authorization, project linking, repository access approval, or other dashboard-only setup may form a later genuine human-only boundary. Such a boundary belongs to a provider-specific adapter Issue and must not be hidden inside this manifest.

## Next increments

F2/F3 may implement narrowly scoped Vercel and Cloudflare workspace adapters against this exact contract, preferably as comparative adapters rather than mutually exclusive architecture. No provider becomes the Foundation default until controlled Phase D/E/F evidence satisfies the program adoption gate.

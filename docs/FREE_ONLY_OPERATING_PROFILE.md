# Free-only operating profile

This profile is the default for this fleet unless the repository owner explicitly authorizes a paid external service.

## Cost boundary

- Existing ChatGPT and Cloudflare subscriptions are the only assumed paid contracts.
- GitHub remains the source-control, Issue, Pull Request, review, and connected-API system.
- GitHub-hosted Actions are optional capacity. Exhausted included minutes, billing blocks, or an unavailable hosted runner are execution-route unavailability, not product-code failure.
- The Foundation must never enable a paid plan, paid overage, payment method, billing feature, or usage-based service automatically.
- OpenAI API usage is outside this profile. ChatGPT is the interactive AI control plane; deterministic build/test/deploy work may run on Cloudflare.

## Private GitHub Actions cost guard

Bootstrap-generated target copies of Foundation-managed GitHub Actions workflows are transformed deterministically so every job has a server-side guard before runner allocation:

`github.event.repository.private == false || vars.FOUNDATION_PRIVATE_ACTIONS_ENABLED == 'true'`

The public Foundation source workflow files are not rewritten by this policy, so the public Foundation repository can continue using its included public GitHub-hosted runners. In a generated private target, an unset `FOUNDATION_PRIVATE_ACTIONS_ENABLED` leaves the Foundation-managed jobs skipped. Bootstrap never creates or modifies this repository variable. The owner may explicitly set it to `true` later to opt back into legacy private Actions capacity.

Product-owned workflows outside the Foundation managed-file set are not rewritten implicitly. Each such workflow needs its own reviewed migration or an explicit decision to retain it.

## Validation architecture

SCM evidence and execution evidence are separate.

SCM evidence remains GitHub-native and must bind the trusted source Issue, same-repository Pull Request, exact head SHA, changed paths, review threads, coordinator review, hold state, and expected-head merge.

Execution evidence may come from an explicitly configured no-additional-cost validator. Cloudflare Workers Builds is the first supported external route. External evidence must fail closed unless all of these identities match:

- exact repository;
- exact Pull Request;
- exact 40-character candidate SHA;
- exact check name;
- exact integration application slug, and application ID when configured;
- completed successful conclusion.

A stale successful run cannot mask a newer pending or failing rerun for the same external validator identity. A commit-only check that is not associated with the exact Pull Request cannot authorize a merge.

## Cloudflare route

For Git-connected projects, prefer Cloudflare Workers Builds inside the account's existing free/contract allowance. Non-production branch builds are the intended Pull Request validation route once enabled for that project. Production deployment remains separately controlled and is never implied by a validation-only change.

A Cloudflare project or check identity is target-owned configuration. Foundation does not guess the GitHub App slug or ID and does not trust a check solely because its display name contains “Cloudflare”.

## No-cost fallback

Some repositories cannot be meaningfully built in Workers Builds. Those repositories remain fail closed until a no-additional-cost validation route is explicitly configured, such as deterministic local validation on an already-owned machine. The Foundation must not silently fall back to paid GitHub-hosted Actions, another paid CI service, or an API-billed AI executor.

## Human-action boundary

A request for payment, a new paid plan, overage authorization, billing setup, or another paid external service is always an explicit owner decision. Automation may report that a no-cost route is unavailable, but must not convert that into an instruction to purchase capacity unless the owner asks for paid alternatives.

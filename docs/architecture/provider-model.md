# AetherGate v2 — Provider, Account, and Routing Model

Living document. Derived from the audit. Distinguish **settled** / **direction** / **deferred**.

## Core entities

| Entity | Meaning | Notes |
|---|---|---|
| Organization / Project | Tenant boundary | First release: one company per deployment with project boundaries. (Settled) |
| Principal | User or service account | OIDC-backed named identity for humans; scoped, expiring service accounts for automation. |
| API credential | Scoped key/secret | Rotatable without resetting quota history; separate from provider credentials. |
| Provider | A backend vendor (OpenAI, Anthropic, Ollama, LiteLLM-compatible, etc.) | Capability + limit profile. |
| Provider account | A distinct billing/quota scope at a provider | **Not identified by base URL** — two accounts can share a URL. (Settled) |
| Secret reference | Pointer to encrypted/envelope-encrypted credential | Decrypting key outside the database. |
| Endpoint / deployment | A physical serving location for an account | Multiple endpoints/credentials can share one provider org/project quota. |
| Public model alias | The name clients use (`gpt-4o`) | Must not mint independent provider capacity. |
| Route binding | Maps alias + capabilities to an endpoint/deployment | Explicit; no implicit data destination. |
| Shared quota group | Shared limit across aliases/endpoints | Two endpoint records sharing one account quota must share the same bucket. |

## Invariants

- Provider account identity is a first-class field, never inferred from URL alone. (Settled)
- Public aliases do not create provider capacity; they resolve through route bindings. (Settled)
- Unknown, deleted, or unbound aliases cannot invoke an upstream or receive accidental free routing. (Settled)
- Deleting an endpoint must not silently unlink models into a fallback backend; it must reject while
  referenced or explicitly disable dependent routes. (Settled)
- Credentials are rotatable without resetting quota history or usage attribution. (Settled)
- Model capability (text/embedding/image/audio) is explicit and gates which routes/adapter paths a
  request may use. (Direction)

## Capability matrix

The gateway publishes an endpoint/model capability matrix rather than claiming the whole OpenAI
platform. Core v2 target: model listing/retrieval, Chat Completions, Responses, embeddings.
Image/audio are advertised only after their routes and adapters pass contract tests. (Direction)

## Routing policy

- Routing is a settled authorization decision made by AetherGate, not a client-supplied destination.
- Ordinary inference callers never choose arbitrary server-side destinations, upstream URLs,
  credentials, or trusted headers. (Settled)
- Approved egress destinations are enforced; intended private LAN ranges are allowed explicitly,
  while metadata endpoints, DNS rebinding, and redirect escapes are blocked. (Settled)

## Provider feedback

- Provider-specific token reservation and reset-window semantics are captured in the provider
  capability/limit profile; do not assume every provider counts input/output or resets daily
  identically. (Direction)
- `Retry-After` and documented reset windows are honored; cooldown applies to the actual shared
  quota scope. (Settled)

## Deferred

- Exact schema for the provider capability/limit profile (fields, granularity).
- Whether shared quota groups are modeled as their own entities or as account-level attributes.
- The operating/commercial model (internal access, project budgets, showback/chargeback, prepaid
  balances, reseller access, or combinations). This is deferred, not settled: do not assume prepaid
  billing is superseded — see `project_spec.md` §Accounting, pricing, and commercial model.

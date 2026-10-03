# AetherGate v2 — Administration API

Living document. Derived from the audit. Distinguish **settled** / **direction** / **deferred**.

## Contract

- Versioned management API under `/admin/v1`. (Settled)
- Typed schemas, stable opaque resource IDs, PATCH semantics, optimistic versioning, pagination,
  filtering, and safe import/diff/apply workflows. (Direction)
- The web console and the Linux CLI consume the **same** contracts and service layer; normal
  administrative tooling never edits the database directly. (Settled)

## Resource coverage

Providers, accounts, secrets, endpoints, quota groups, model aliases, routes, users, service
accounts, keys, budgets, queues, audit, and configuration. (Settled)

## Semantics

- Invalid IDs/values yield `4xx`; omitted vs `null` fields behave predictably; conflicting edits are
  detected via optimistic versioning. (Settled — AG-022.)
- Stable opaque resource IDs in administrative paths; public model names are data, not path segments,
  so aliases containing any permitted character work without changing spelling or case. (Settled — AG-023.)
- Lists are server-paginated and filtered; imports are typed, versioned, previewed/diffed, and
  authorized; privileged overwrite behavior is explicit. (Settled — AG-024.)
- Secrets are returned only at explicit create/rotate boundaries, never in normal reads or exports. (Settled)

## Auth model

- Administrative endpoints authenticate a named principal (OIDC/session or service account), not a
  shared master secret. (Settled)
- The v1 `x-admin-key` master-key scheme is superseded; it exists only as a one-use bootstrap path. (Direction)

## Audience separation

Administrative and inference permissions are separate; the same credential should not gate both
surfaces. (Settled)

## Deferred

- Concrete schema/OpenAPI layout for `/admin/v1` (owned by the `contracts` workstream, established first).
- Import/export format and the diff/apply UX details.
- Whether `PATCH` (RFC 7386) or typed PATCH bodies are used per resource — decided with contracts.

# Admin v1 Contract Foundation

Living document. Records the initial `/admin/v1` DTO foundation and the choices that
remain unresolved. The concrete DTOs live in
`src/aethergate/contracts/admin_v1.py`; this document describes intent and deferred
decisions. FastAPI routes, paths, and filters are **not** finalized yet.

## Established DTO shapes

Read/create/update foundations exist for: providers, provider accounts, endpoints, quota
groups, model aliases, route bindings, projects, principals, and API credential metadata.

- Create shapes carry required fields plus defaults.
- Read shapes carry stable opaque IDs and no secret material.
- Update shapes use explicit optionality (all fields optional) for PATCH semantics.

## Requirements honored

- Stable opaque resource IDs (typed `*Id` values, no database auto-increment or table names).
- Explicit optionality: omitted vs `null` are distinct and modeled via `Optional` fields.
- No secret values on normal read DTOs; secret material is referenced via `SecretRefId`.
- No database-specific fields (no table names, integer PKs, or ORM state).
- No business logic in DTOs (validation only).
- No assumption that the web console is the only client.

## Deferred decisions

The following are intentionally unresolved and will be finalized with the `contracts`
workstream and integrator:

- Concrete routes/paths and HTTP verbs per resource.
- Filtering, sorting, and server-pagination query conventions.
- Optimistic versioning / concurrency-token header name and semantics.
- The one-time secret reveal shape for API credential creation/rotation (the material is
  never placed in a read DTO; the reveal contract is a create/rotate boundary concern).
- Import/diff/apply ("safe import") payload format and preview UX.
- `PATCH` body style (RFC 7386 vs typed partial bodies) per resource.

## Relationship to runtime

The admin API will be thin routers over service contracts; these DTOs are the persistence-
and transport-independent foundation those routers will consume.

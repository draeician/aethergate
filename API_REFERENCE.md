# AetherGate API Reference — retired

The previous hand-written `API_REFERENCE.md` documented the retired v1 API
(`sk-` keys, `x-admin-key`, prepaid-balance authorization, and the old
`/admin/*` routes). It is no longer accurate and has been retired.

The current AetherGate API is defined by the running application and the
versioned contracts in the repository:

- Generated OpenAPI document: `frontend/src/generated/openapi.json`
  (regenerate with `python scripts/gen_openapi.py`).
- Inference (OpenAI-compatible) surface: `docs/contracts/`
  (`openai-compatibility-baseline.md`, `responses.md`).
- Management API (`/admin/v1`): `docs/architecture/admin-api.md` and
  `docs/contracts/admin-v1-foundation.md`.
- Operator runbook: `docs/operations/alpha-runbook.md`.

Credentials are AetherGate-scoped (`agk_...` inference, `ags_...` service
accounts, `agb_...` one-use bootstrap) and authenticate `/v1` or `/admin/v1`
respectively. A positive monetary balance is not an authorization requirement.

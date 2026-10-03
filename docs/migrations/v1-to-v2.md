# AetherGate v1 -> v2 Migration

Living document. Derived from the audit. Distinguish **settled** / **direction** / **deferred**.

## Direction

- Move from SQLModel/SQLite + `create_all()` + ad-hoc scripts to PostgreSQL with versioned,
  framework-managed migrations. (Direction)
- Migrations are explicit and reversible where practical. (Settled)

## What the v1 schema holds today (audit evidence)

- `User` (balance, org free-text, `is_active`), `APIKey` (hash-verified `key_hash`, `log_content`
  default true, per-key rate limit string), `LLMEndpoint` (plaintext `api_key`, rpm/day limits),
  `LLMModel` (public `id` = PK, `litellm_name`, prices, fallback, rpm/day overrides), `RequestLog`.
- v1 has no project, provider-account, quota-group, reservation, ledger, or audit-event entities.

## Identity preservation (settled)

- Preserve account identity and historical usage attribution across migration.
- Never infer provider-account identity from URL alone. (Settled — AG-026: endpoint migration
  deduplicated only by base URL, potentially merging different accounts.)
- Key rotation / deletion must not orphan or erase spend history. (Settled — AG-021.)

## Migration procedure (direction)

1. Run against a copy of the real old schema (never the live DB directly).
2. Map v1 `User`/`APIKey`/`LLMModel`/`LLMEndpoint`/`RequestLog` into the v2 domain model with
   explicit, recorded mappings (identity, ownership, and usage references).
3. Preserve secrets via the new secret-reference mechanism rather than plaintext columns.
4. Convert floating-point balances/costs to decimal/fixed-point; preserve spend history.
5. Test rollback and cutover controls before production.

## Known v1 traps to carry into the mapping

- Deletion of users/keys cascade-deletes `RequestLog` (audit AG-021); v2 must use revocation/
  tombstones or key-secret versions with stable identity.
- Migration scripts `scripts/migrate_endpoints.py`, `scripts/add_model_config_columns.py`,
  `scripts/add_rate_limit_column.py` open local `aethergate.db` paths rather than the configured
  deployed DB (AG-026); they are superseded by versioned migrations.
- `init_db.py` prints errors without a failing exit; fresh deployment must run a one-shot migration
  stage with a nonzero failure status. (AG-027.)

## Deferred

- Exact migration framework and revision-graph ownership (the revision graph is a shared area;
  changes go through the integrator).
- Reconciliation strategy for v1 rows that cannot be mapped unambiguously (e.g., accounts merged
  by URL in old data).

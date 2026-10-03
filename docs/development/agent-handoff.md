# AetherGate Agent Handoff

## Current State
- Branch: v2
- Starting commit: 0982ed7598b24efd371443eacdd3cf8b325b8664
- Implementation commit: 6a4be47ba03f40568b247060dafe2bec11bb0480

## Task Completed
AGV2-002 — established the first executable v2 foundation: a Python package skeleton
(`src/aethergate`) with typed resource IDs, enums, monetary value objects, cross-domain
entity contracts, and initial `/admin/v1` DTOs, plus the pinned OpenAI compatibility
baseline and a deterministic offline test suite. No persistence, scheduler, provider,
auth, billing, or HTTP routes were implemented.

## Changes
- `src/aethergate/` — new package: `domain/{ids,enums,value_objects,entities}.py`,
  `contracts/{common,admin_v1}.py`, `__init__.py`, `py.typed`.
- `pyproject.toml` — minimal setuptools packaging + pytest/ruff config; pydantic>=2.7,<3.
- `tests/` — 4 test modules (15 tests), deterministic and offline.
- `docs/contracts/{openai-compatibility-baseline,admin-v1-foundation,domain-model}.md` — new.
- `.gitignore` — added `*.egg-info/`.

## Contracts established
- 17 typed opaque resource IDs (`str` subclasses, distinct runtime types, reject empty).
- Enums: `Capability`, `BillingUnit`, `PrincipalKind`, `RequestState` (includes
  `outcome_unknown`), `ExecutionAttemptState`, `LedgerEntryType`.
- Value objects: `Money` / `NonNegativeMoney` (fixed-point `Decimal`; `float` rejected).
- Entities: identity/access, catalog/routing, scheduler/execution, accounting/audit.
- Admin DTOs: read/create/update for providers, accounts, endpoints, quota groups, model
  aliases, route bindings, projects, principals, API credentials (no secret material on reads).

## Decisions
- OpenAI baseline pinned to `openai/openai-openapi` @ `f7bc81b7` (spec 2.3.0, 2026-10-03).
- Target endpoints: `/v1/models`, `/v1/models/{model}`, `/v1/chat/completions`,
  `/v1/responses`, `/v1/embeddings`; responses preferred, chat completions supported.
- Pydantic v2; `Decimal` for money; IDs are typed `str` subclasses (not DB ints).
- Entities/DTOs are contracts only (no business logic, no ORM, no secrets on reads).

## Deferred
- OpenAI external ID prefix/format (UUID internally is fine).
- Admin route/path/filter conventions; optimistic-versioning header; one-time secret
  reveal shape; import/diff/apply payload; PATCH body style (see `admin-v1-foundation.md`).
- A dedicated type checker (mypy/pyright); only ruff lint is introduced so far.

## Verification
- `.venv/bin/python -m pytest -q` → 15 passed.
- `.venv/bin/ruff check src tests` → All checks passed.
- `git diff --check` → clean.
- `app/` and `frontend/src/` untouched; `docs/audits/v2-architecture-audit-2026-10-03.md` unchanged.

## Issues / Risks
- Pre-commit secret scan flags `api_key=...`/`sk-...` literals; keep secret-adjacent test
  fixtures name- and value-neutral (a false positive blocked the first commit attempt).
- No CI yet; tests/lint run locally only. A CI workflow is still a future platform task.

## Recommended Next Step
Model the database persistence layer (PostgreSQL + versioned migrations) for these domain
entities, keeping the contracts unchanged and coordinated with the `platform/migrations`
workstream.

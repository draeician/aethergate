# Claude Code Configuration

AetherGate is a self-hosted, OpenAI-compatible inference gateway (FastAPI +
PostgreSQL + Alembic). The canonical runtime is `src/aethergate/`; deploy is
`deploy/v2/`; helpers are `scripts/dev/v2` and `scripts/ops/v2`.

Read the authoritative sources in order:

1. `AGENTS.md`
2. `project_spec.md`
3. `docs/development/current-task.md` and `docs/development/agent-handoff.md`
4. `docs/architecture/`

Use `pytest` (via `scripts/dev/v2 test`) for tests and the `aethergate` CLI for
operations. Do not use SQLModel, SQLite, prepaid-balance authorization,
`MASTER_API_KEY`, or `x-admin-key`; those are retired v1 concepts.

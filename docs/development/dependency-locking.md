# Python Dependency Locking (Alpha 1)

AetherGate v2 keeps `pyproject.toml` dependency ranges human-readable (the allowed
version contract) and pins the **one concrete resolution that is known green** in
the committed `constraints.txt` at the repository root.

## Why

Generated artifacts must not depend on whichever version pip happens to resolve
on a given day. In AGV2-024, CI resolved a newer Pydantic than the environment
that generated the committed OpenAPI, which changed the generated `Decimal`
schema (dropping a Pydantic-generated `pattern`) and failed the
`frontend`/`schema-secret-gate` drift checks. Locking the resolved graph makes
CI, Docker builds, and local installs byte-identical.

## Strategy

- `pyproject.toml` — compatible ranges (`pydantic>=2.7,<3`, `fastapi>=0.115,<1`,
  …). This stays the human-facing contract of what is allowed.
- `constraints.txt` — a pip **constraints** file pinning every runtime and dev
  transitive dependency to the exact known-green version. Everything after the
  `# BEGIN LOCKED VERSIONS` marker is machine-managed.
- The lock is consumed everywhere the environment must be deterministic:
  - **CI**: `.github/workflows/backend.yml` and `.github/workflows/frontend.yml`
    install with `-c constraints.txt`; `alpha-golden-path.yml` does the same on
    the runner.
  - **Docker**: `deploy/v2/Dockerfile` runs
    `pip install -c constraints.txt ".[dev]"`.
  - **Local canonical path**: `pip install -e ".[dev]" -c constraints.txt`.

`constraints.txt` never contains an editable path, a credentials URL, or the
`aethergate` package itself (which is always installed editable from the working
tree).

## Refreshing the lock

Refreshing dependencies is an explicit, reviewable change — never implicit.

```bash
scripts/ci/refresh_lock.sh          # rebuilds the pinned block from a clean venv
python scripts/gen_openapi.py       # regenerate the canonical schema
(cd frontend && npm run generate:client)
# then run the full local gates (backend/frontend/Playwright/ruff/drift/…)
git add constraints.txt frontend/src/generated/
git commit -m "build: refresh locked python dependencies"
```

`refresh_lock.sh` installs `pyproject.toml` ranges into a throwaway virtualenv and
rewrites only the pinned block between the header and the marker; the explanatory
header is preserved. Because a dependency refresh can legitimately change the
generated OpenAPI, the same commit must include any regenerated contract and a
green gate run.

## Relationship to OpenAPI drift

The generated `frontend/src/generated/openapi.json` and `schema.d.ts` are checked
for drift in CI. Under the lock, generation is reproducible: generating twice from
the locked environment produces zero diff. If a lock refresh changes the generated
schema, inspect the change (especially Decimal string handling), keep the exact
Decimal-string wire guarantee, commit the regenerated output, and prove a second
generation produces no diff.

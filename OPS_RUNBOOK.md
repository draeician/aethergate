# AetherGate Operations Runbook — moved

The previous `OPS_RUNBOOK.md` described the retired v1 deployment (docker02,
the Streamlit dashboard, `MASTER_API_KEY`/`x-admin-key`, and old NPM/port
instructions). It is historical and unsafe as current operational guidance, so
it has been retired.

Use the current operator runbook instead:

- **`docs/operations/alpha-runbook.md`** — clean install, secrets, bootstrap,
  OIDC, catalog/provider setup, credentials, health/readiness, backup/restore,
  upgrade, restart/recovery, and failure modes.

Related current references:

- Architecture: `docs/architecture/`
- Alpha status: `docs/releases/alpha1-readiness.md`
- Development workflow: `docs/development/README.md`

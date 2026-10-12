# AetherGate Git Policy

This file is project-specific and overrides generic crules Git boilerplate when they conflict.

## Branches

- `main` is the canonical, protected release branch.
- `develop` is the active integration/development branch for new agent work.
- `v2` is historical after the canonical cutover. Do not base new tasks on it and do not push new work to it.
- The `v2` component in `deploy/v2/`, `scripts/dev/v2`, and `scripts/ops/v2` is historical path naming, not the active Git branch.

## Task workflow

1. New scoped work starts from the latest `origin/develop`.
2. The first local repository action for a task launched from `docs/development/current-task.md` is creating the gitignored `.aethergate-wip` marker required by that task.
3. Pull/read `AGENTS.md`, `project_spec.md`, `docs/development/current-task.md`, and `docs/development/agent-handoff.md`.
4. Execute the task completely, update the handoff, commit with conventional commits, and push to `origin/develop`.
5. Required GitHub Actions must be green on the exact final `origin/develop` SHA.
6. Open/update a reviewed `develop` -> `main` pull request when the task requires it.
7. Never push directly to `main`.
8. Never merge a `develop` -> `main` PR without the project's explicit reviewed/user-authorized merge step.
9. After an authorized merge, verify post-merge CI on the exact resulting `main` SHA, then fast-forward/synchronize `develop` to that `main` commit before queueing the next task.

## Safety

- Never commit secrets, credentials, provider keys, `.env` files, private keys, or `.aethergate-wip`.
- Run the repository secret/canary scan required by the active task before completion.
- Do not bypass required CI, tests, migrations, branch protections, or review gates.
- Do not use the generic crules release/version-bump shortcut unless an explicit AetherGate release task requires it.

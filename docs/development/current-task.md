# AetherGate — Active Task

## Identity

- **Task:** AGV2-032 — LAN + Tailscale access, model discovery capabilities, model/endpoint
  administration, and per-route thinking controls for the Beta 1 pilot.
- **Status:** implementation + live acceptance complete; PR open for independent review (not merged).
- **Repository:** `draeician/aethergate`
- **Only task branch:** `feat/agv2-032-lan-tailscale-admin-thinking`, created exactly from post-AGV2-031
  `origin/main` `79838ec65a46a8c6936ca0176330507bf2155984`.
- **Historical `v2` branch:** retired. Never push to it or directly to `main`.

## FIRST local repository action: WIP marker

The gitignored root `.aethergate-wip` (task `AGV2-032`, branch, UTC start) is created and kept until all
completion gates, the handoff, and final-head PR CI pass. Never commit it or include credentials. Follow
`AGENTS.md` and `project_spec.md`; reread this file and `docs/development/agent-handoff.md` after
compaction.

## Scope delivered

- **Per-route thinking policy** (migration `0018`): `RouteBinding.thinking_policy`
  (`inherit`/`off`/`on`/`low`/`medium`/`high`) resolved at dispatch into upstream `reasoning_effort`.
  Explicit administrative policy always overrides a client-supplied `reasoning_effort`; `low`/`medium`/
  `high` are rejected at configuration time unless the upstream can represent a distinct level
  (LiteLLM Ollama forwards a level only for `gpt-oss`). New module `src/aethergate/thinking.py`.
- **Model discovery capabilities:** additive `capabilities: list[str]` on the OpenAI `/v1/models`
  `ModelObject`; no routing/providing leak.
- **Admin CLI:** `create-provider-account`, `create-endpoint`, `create-model-alias`,
  `create-route-binding` (`--thinking-policy`), `update-route-binding`, `credentials create`.
- **LAN + Tailscale access front-end:** `scripts/ops/beta1-access` (socat systemd --user unit for
  `<LAN_IP>:8088` plus `tailscale serve --https=443` for the MagicDNS name) and
  `docs/operations/beta1-access.md`. Loopback-only API bind is preserved; Funnel is never enabled.
  `deploy/beta1/compose.yaml` now honours `AETHERGATE_API_HOST_PORT` for a stable upstream port.

## Completion gates

- `scripts/dev/v2 test` green (900 passed), `ruff check src tests`, `git diff --check`, secret scan,
  frontend lint/test/build/`tsc -b --noEmit` (89 passed), isolated `scripts/ci/beta_release_gate.sh`
  green (empty -> `0018`, outage/recovery, backup/restore -> `0018`).
- Live nomnom pilot migrated to `0018`; LAN `8088` + loopback SDK checks; `qwen3-2b` set to
  `thinking_policy=off` (clean output non-stream/stream/with client override; config-time rejection of
  `low`; persists across API/worker restart).
- On the **exact final PR head** require all five PR-event workflows completed/success.

## PR and phone alert

Push scoped commits to `feat/agv2-032-lan-tailscale-admin-thinking`, open a PR to `main`, and make it
ready for independent review **without merging it**. Do not tag, publish, or deploy. `main` has no
branch protection; do not misrepresent that as resolved.

**Operator-only blocker:** Tailscale Serve is not enabled on the tailnet
(`https://login.tailscale.com/f/serve?node=nwjx2TALce11CNTRL`). The LAN listener works; the tailnet
HTTPS listener activates once the operator enables Serve and re-runs `scripts/ops/beta1-access install`.

**Standing notification rule:** send one urgent `https://ntfy.sh/draeician_alerts` alert only when
AGV2-032 is fully complete and independently review-ready, with PR link and exact SHA; record delivery
in the PR conversation without a final-head-changing commit.

## Completion response

State final branch SHA, five green PR CI run IDs, live acceptance evidence/limits (including the Serve
blocker), phone alert receipt, and the PR URL.

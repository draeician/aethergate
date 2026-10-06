# AetherGate Linux CLI

The `aethergate` command is an installable Linux client that is a **thin HTTP client over the same
`/admin/v1` API** the web console uses. It never imports persistence/repository modules and never
touches the database directly. Human login uses an RFC 8628 OAuth device flow; automation uses
admin service credentials.

This document is the living CLI reference. Distinguish **settled** / **direction** / **deferred**.

## Installation

The package declares a console entry point in `pyproject.toml`:

```bash
pip install -e .
aethergate --version   # -> "aethergate, version 0.1.0" (matches aethergate.__version__)
aethergate --help
```

`--version` and `--help` work without any server or configuration present.

Runtime dependencies are deliberately small: `click` (CLI framework), `keyring` (protected OS
credential store), and `tomli-w` (TOML profile writing). Profiles are read with the stdlib
`tomllib`.

## Profiles

Profiles live under the XDG config directory (`~/.config/aethergate/config.toml` on Linux) and
contain **no secrets** — never a bearer token, credential, or provider key:

| Command | Purpose |
|---|---|
| `aethergate profile create <name> <base_url> [--ca-bundle PATH] [--insecure] [--default]` | Create a profile. |
| `aethergate profile list` | List profiles (`default_profile` + per-profile base URL / CA bundle). |
| `aethergate profile show [name]` | Show one profile (default profile if omitted). |
| `aethergate profile delete <name>` | Delete a profile **and** its stored token entry. |
| `aethergate profile set-default <name>` | Change the default profile. |

Rules:

- `base_url` is validated. Remote (non-localhost) profiles must be `https://` unless `--insecure`
  is passed explicitly; `--insecure` is a labeled dev/test escape hatch and is never silently
  saved as a production default. `http://` on loopback (`127.0.0.1`/`localhost`) is allowed for
  local stacks.
- A trusted custom CA bundle is supported via `--ca-bundle PATH` and is passed to TLS verification
  per request (system trust when omitted).
- Profiles select the API base URL and TLS behavior only; there is no arbitrary header/secret
  proxying.

## Authentication

### Human device login (OAuth device flow)

```
aethergate auth login [--no-store] [--json]
```

The CLI asks the gateway to start a device transaction (`POST /admin/v1/auth/device/start`), prints
the verification URL + user code to **stderr**, then polls (`POST /admin/v1/auth/device/poll`) at the
provider-recommended interval. It never prints the raw `device_code`. On success it stores the CLI
session token in the protected token store (unless `--no-store`) and prints the result.

Poll status handling: `pending` keeps polling; `slow_down` increases the local interval by the same
+5s/60s cap the server enforces; `success` yields the one-time raw CLI session token; a terminal
failure raises a stable error.

### Service credential (automation)

```
aethergate auth set-token --stdin      # read the admin credential from stdin
aethergate auth set-token              # hidden interactive prompt
```

The credential is validated against `GET /admin/v1/whoami` **before** it is stored, so an
inference-audience credential is rejected for admin CLI use. Raw credentials are never accepted as a
normal argv option and never echoed.

An `AETHERGATE_TOKEN` environment variable may override storage for the current process only (CI);
it is never persisted automatically.

### Other auth commands

- `aethergate auth whoami [--json]` — resolve the current CLI session / service credential
  (`authentication_kind` is `cli_session` or `service_credential`).
- `aethergate auth logout [--json]` — revoke the server-side session (`POST /admin/v1/auth/cli/logout`,
  idempotent) and delete the local token.

## Protected token storage

Persistent tokens are stored through a `TokenStore` abstraction backed by the OS credential store
(`keyring`: Secret Service / KWallet on Linux), **not** plaintext profile TOML.

- The human CLI session token is stored under the profile name after a successful login.
- A service credential may be stored under the profile via `auth set-token`.
- `auth logout` revokes the session and removes the local token; `profile delete` removes its token
  entry.
- If no usable keyring is available, the CLI **fails safely** with an actionable message and does
  not silently fall back to plaintext disk; `auth login --no-store` provides an explicit ephemeral
  in-process mode.
- Tests use an in-memory `MemoryTokenStore`; the keyring implementation verifies each write with a
  read-back round trip before trusting it.

## Output and exit codes

Every command supports `--json` for machine-readable output; the default is human-readable.

- `--json` writes **exactly one JSON document to stdout** and nothing else. Progress text, the
  device-login verification prompt, and all human diagnostics go to **stderr**.
- Python tracebacks are never dumped for expected failures.

Stable exit codes (documented and tested):

| Code | Category |
|---|---|
| `0` | success |
| `1` | generic failure |
| `2` | usage / validation error |
| `3` | authentication required / expired / rejected |
| `4` | authorization denied |
| `5` | not found |
| `6` | conflict / invalid lifecycle |
| `7` | network / TLS failure |
| `8` | server / internal failure |

The HTTP client maps AetherGate structured errors (`{"error":{"code",...}}`) to these codes; a
device-flow terminal code (`device_expired`, `device_access_denied`, `device_code_invalid`) maps to
the authentication code (`3`).

## HTTP client behavior

One reusable client (`aethergate.cli.client.Client`) provides:

- profile base URL + trusted CA (`verify`);
- `Authorization: Bearer <token>` injection from the token store or env;
- JSON request/response with a request timeout;
- structured AetherGate error parsing into typed CLI errors;
- retries **only** for idempotent `GET` on transport failures; `POST`/`PATCH` mutations are never
  retried automatically;
- a `User-Agent` carrying the CLI version;
- no sensitive headers in any debug output.

## Command coverage

Core: `profile`, `auth login`/`logout`/`whoami`/`set-token`, `completion`.

- Projects: `projects list/show/create/update`.
- Principals: `principals list/show/create`.
- Credentials (metadata only): `credentials list/show`.
- Role assignments: `role-assignments list/show`.
- Catalog: `providers list/show`, `create-provider`, `provider-accounts list/show`,
  `endpoints list/show`, `model-aliases list/show`, `route-bindings list/show`,
  `price-policies list/show`.
- Accounting: `budgets list/show/status`, `usage list/show`, `ledger list/show`, `audit list/show`.
- Queue/operator: `queue list/show/summary/cancel/outcome-unknown/reconcile/quota-status`,
  `endpoint list/show/pause/drain/resume`.

There is **no** generic "POST arbitrary JSON to an arbitrary path" escape hatch. Operator mutations
(`pause`/`drain`/`resume`/`cancel`/`reconcile`) call the typed server endpoints and are subject to
the same server-side RBAC; the CLI never bypasses server checks.

**Direction (follow-up CLI coverage task):** create/update for endpoints, model aliases, and route
bindings are not yet exposed as CLI write commands (only `providers` has `create-provider`). The
backend CRUD surface exists; the CLI write expansion is intentionally deferred rather than exposed
through an unsafe passthrough.

## Shell completion

```
aethergate completion bash
aethergate completion zsh
aethergate completion fish
```

Renders a completion script without contacting the server (no root installation required).

## Exit-code / secret contract summary

- No token (CLI session, service credential, provider secret, device code, ID token) is ever written
  to the profile config, printed in normal output, logged, or accepted from argv.
- JSON stdout stays pure; stderr carries human diagnostics.

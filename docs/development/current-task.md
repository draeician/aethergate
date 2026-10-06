# AetherGate v2 — Current Task

## Task ID
AGV2-019V

## Title
Close CLI token-handling and protected-storage gaps

## WIP Marker — FIRST LOCAL ACTION

Immediately after entering the repository, before pull/read/implementation work, create:

`.aethergate-wip`

Safe contents:
- task ID: AGV2-019V
- branch: v2
- UTC start timestamp

It is gitignored. Never stage/commit/push it.
Keep it present while this task is incomplete.
Remove it only after all verification is green, the handoff is committed, every commit is pushed to
origin/v2, and origin/v2 is verified.

If blocked/incomplete, leave it present.

## Why This Task Exists

AGV2-019 is substantially implemented and pushed:
- OAuth device flow;
- durable CLI sessions;
- migration 0016;
- installable Linux CLI;
- profiles/CA support;
- keyring-backed token storage;
- admin API command coverage;
- 470 passing tests;
- live device-flow/RBAC/operator/SDK verification.

Final code review found three narrow CLI contract violations:

1. `aethergate auth login` emits the entire successful device-poll response. That response contains
   the one-time raw `ags_...` CLI session bearer token, so the token is printed to stdout in both
   human and `--json` modes.
2. `KeyringTokenStore` does not translate real keyring backend exceptions
   (`NoKeyringError`, locked/unavailable backend, etc.) into the CLI's safe
   `TokenStoreUnavailable` error, so some Linux hosts can get a raw traceback.
3. The CLI requirement says an expired/revoked human CLI session token should be cleared from
   protected storage on an authentication `401` when appropriate. The code defines the idea but
   does not wire it through. A stored stale `ags_...` token remains in the keyring.

Close these only. Do not redo AGV2-019 or advance to the web console yet.

## Recovery

If context is compacted/restarted/uncertain:
1. ensure `.aethergate-wip` exists if task incomplete;
2. re-read AGENTS.md;
3. re-read project_spec.md;
4. re-read this file;
5. re-read docs/development/agent-handoff.md;
6. inspect git status/history;
7. continue from repository state.

## 1. Never print the raw CLI session token

The server device-poll success response must continue to return the raw session token exactly once so
the CLI can store it.

The product CLI must consume that token internally and MUST NOT emit it on stdout or stderr.

For successful `aethergate auth login`:

### Human output
Print only safe confirmation such as:
- authenticated principal / authentication kind;
- roles;
- token stored successfully;
- expiry if useful.

Never print:
- `token`;
- Authorization header;
- device_code;
- provider tokens.

### JSON output
Return a safe machine-readable success object that excludes the raw bearer token.

Preferred shape:
- status;
- stored: true;
- session safe metadata;
- expires_in.

The raw token must be removed before calling the generic output formatter.

Add explicit tests proving a canary raw `ags_...` token is absent from:
- human stdout;
- human stderr;
- `--json` stdout;
- `--json` stderr.

Also prove JSON stdout remains one valid document.

### --no-store

The current one-shot `--no-store` flag uses an in-memory store that disappears when the command
exits and therefore does not provide a useful subsequent authenticated CLI command.

Do not expose the token merely to make `--no-store` useful.

Choose one safe behavior:
- remove/deprecate `--no-store` for this one-shot CLI; or
- retain it only if it has a meaningful secure same-process workflow.

No raw token printing is allowed in either case.

## 2. Translate keyring backend failures safely

Harden `KeyringTokenStore`.

For get/set/delete:
- catch keyring backend exceptions such as `keyring.errors.KeyringError` /
  `NoKeyringError` and equivalent backend failures;
- never expose a Python traceback through the console entry point;
- raise the existing `TokenStoreUnavailable` with an actionable non-secret message where the
  operation requires the protected store;
- do not silently fall back to plaintext disk.

For delete:
- distinguish "credential not found" from "backend unavailable" if the keyring library exposes that
  distinction;
- absence may be treated idempotently;
- backend failure must not be silently represented as successful removal when the command relies on
  removal for its security/postcondition.

Tests must simulate:
- no backend;
- backend get failure;
- backend set failure;
- backend delete failure;
- missing credential delete;
- no traceback;
- no token included in error text.

## 3. Clear stale stored human CLI sessions on 401

When a command authenticates using a persisted human CLI session token (`ags_...`) and the gateway
returns the fixed authentication-required `401`:

- remove that token from the profile's protected TokenStore;
- return the normal auth error / exit code 3;
- emit a safe message that re-login is required;
- do not retry the mutation;
- do not remove a stored service-account credential (`agk_...`) merely because it receives a 401;
- do not mutate token storage for network, 403, 404, 409, or 5xx errors.

If `AETHERGATE_TOKEN` supplied the token:
- do not modify persistent keyring state based on the env override;
- report the authentication failure normally.

Implement this at a centralized CLI request/auth boundary rather than duplicating it across commands.

Add tests:
1. persisted `ags_` + 401 => token deleted;
2. persisted `agk_` + 401 => token retained;
3. env `ags_` + 401 => stored profile token untouched;
4. `ags_` + 403/404/409/500/network => token retained;
5. deletion backend failure => safe actionable error, no traceback.

## 4. Logout local-removal correctness

Review `auth logout`.

Requirements:
- server-side CLI session revocation still happens first;
- successful server revoke removes the local persisted human CLI token;
- already-revoked/expired human session behavior is documented and does not trap the user with an
  undeletable stale local token;
- service-account credential is not sent to the CLI-session logout endpoint as if it were a human
  session;
- provide an explicit safe local credential removal path if necessary (e.g. auth clear-token), rather
  than overloading human logout incorrectly.

Keep scope narrow.

## 5. WIP marker verification

At start:
- `.aethergate-wip` exists;
- `git check-ignore .aethergate-wip` succeeds;
- it is untracked/unstaged.

At completion, only after push verification:
- remove it;
- verify absent locally;
- verify it never appeared in tracked/staged/committed files.

GitHub already proves the previous marker was not committed; this task must follow the same protocol.

## Regression

Re-run:
- full containerized suite (baseline 470);
- CLI unit tests;
- device-flow/CLI-session tests;
- migration head remains 0016;
- browser OIDC/session regressions;
- queue/catalog/accounting admin regressions;
- official OpenAI SDK non-stream + stream with inference bypass false.

No migration expected.

Do not modify migrations 0001-0016.

## Documentation

Update:
- docs/cli.md;
- docs/development/agent-handoff.md;
- any security/admin document only if behavior text needs correction.

Document:
- login output never contains the raw CLI token;
- protected keyring backend failure behavior;
- stale human-session token clearing rule;
- logout/local-token semantics;
- explicit no-migration decision;
- WIP marker removed only after final push verification.

## Handoff

Include:
- implementation commit(s);
- login token-redaction proof;
- keyring failure handling proof;
- stale ags_ 401 clearing proof;
- agk_ retention proof;
- env-token behavior;
- logout/local-clear semantics;
- final test count;
- real device login regression;
- real SDK regression;
- migration head 0016/no migration;
- WIP marker lifecycle;
- exactly one recommended next step.

Never include raw CLI/device/API/OIDC/session tokens or provider secrets.

## Commit and Push

Suggested commit:
`fix(cli): protect login tokens and stale session storage`

Push all completed commits to origin/v2.
Never push directly to main.
Do not ask whether to commit/push.

The task is complete only when every criterion above is green, origin/v2 contains the final
implementation/tests/docs/handoff, and local `.aethergate-wip` has been removed after push
verification.

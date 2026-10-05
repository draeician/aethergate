# AetherGate v2 — Current Task

## Task ID
AGV2-015C

## Title
Close OIDC transaction-cookie failure-path semantics

## Why This Task Exists

AGV2-015V correctly added browser binding for OIDC login transactions, removed raw state from audit,
and passed 359 tests plus live nomnom verification.

Final review found one narrow acceptance gap:

- the transaction cookie is cleared on successful callback and on post-consume failures such as token
  exchange / ID-token / identity failure;
- but pre-consume callback failures still go through exception handlers without clearing
  `ag_oidc_txn`.

Examples:
- missing code/state;
- provider `error` callback;
- wrong/unknown state;
- missing binding cookie;
- wrong binding cookie;
- replay after the transaction row is gone.

AGV2-015V required failed callbacks to clear the transaction cookie where response handling permits.
Close that behavior and nothing else.

## Recovery

If context is compacted or uncertain, re-read:
1. AGENTS.md
2. project_spec.md
3. this file
4. docs/development/agent-handoff.md

Then inspect git status/history and continue. current-task.md is authoritative.

## Required Behavior

For every OIDC callback response that fails before authenticated session creation:

- return the existing correct structured error/status;
- emit a deletion Set-Cookie for `ag_oidc_txn` using the exact transaction-cookie path;
- never set or create `ag_session`;
- never expose or log the raw transaction cookie;
- never put raw state/nonce/code/PKCE material into audit.

Cover at least:
- provider `error` parameter;
- missing `code`;
- missing `state`;
- unknown/wrong state;
- missing browser-binding cookie;
- wrong browser-binding cookie;
- callback replay after transaction consumption.

## Important State Semantics

Do **not** delete or consume an otherwise-valid server-side login transaction merely because a callback
arrives with a missing/wrong browser-binding cookie.

Reason:
- an attacker/cross-browser callback must not invalidate the legitimate initiating browser's pending
  login transaction.

Therefore:
- failed browser receives a cookie-clear response;
- valid DB transaction remains usable by the legitimate browser until successful consumption or TTL
  expiry;
- unknown/wrong state likewise must not mutate unrelated login transactions.

Successful consumption remains delete-on-consume as implemented.

## Implementation Direction

Prefer a single callback failure-response helper or narrowly scoped exception handling in
`src/aethergate/api/session_auth.py` so cookie clearing is not duplicated inconsistently.

Preserve:
- current error codes/statuses;
- browser-binding verification;
- cross-browser rejection;
- audit secrecy;
- callback replay prevention;
- session fixation behavior;
- CSRF behavior;
- Bearer-vs-cookie precedence.

No migration is expected. Do not modify migrations 0001–0013.

## Automated Tests

Add coverage for at least:

1. provider error callback clears transaction cookie;
2. missing code clears transaction cookie;
3. missing state clears transaction cookie;
4. wrong state clears transaction cookie;
5. missing binding cookie clears transaction cookie;
6. wrong binding cookie clears transaction cookie;
7. replayed callback clears transaction cookie;
8. no failed pre-consume path creates BrowserSession;
9. wrong/missing binding does not delete the legitimate pending login-state row;
10. legitimate initiating browser can still complete that still-pending transaction after a separate
    browser made a failed binding attempt;
11. raw state/binding/code remain absent from audit/log output;
12. existing 359-test baseline remains green or higher.

## Live Nomnom Verification

Using the local/internal deterministic IdP:

1. Browser A starts a login and holds the real transaction cookie.
2. Browser B submits A's valid state/code without the cookie:
   - fails;
   - receives transaction-cookie deletion;
   - creates no session;
   - A's pending DB transaction remains.
3. Browser A then completes the same pending login with its valid cookie:
   - succeeds.
4. Browser A starts a fresh transaction and submits wrong state:
   - fails;
   - response clears its transaction cookie;
   - no authenticated session is created.
5. Confirm happy-path OIDC login still succeeds.
6. Run official OpenAI SDK non-stream + stream inference with inference auth bypass disabled.

## Verification

- full containerized suite;
- ruff/lint;
- git diff --check;
- secret/state/token canary scan;
- migration head remains 0013;
- legacy v1 untouched;
- dated audit unchanged.

## Handoff

Update docs/development/agent-handoff.md with:
- implementation commit;
- exact failure paths now clearing the transaction cookie;
- proof that wrong/missing binding does not invalidate the legitimate pending transaction;
- live Browser B failure -> Browser A success proof;
- final test count;
- dynamic AetherGate port/backend;
- explicit no-migration decision;
- exactly one recommended next step.

Do not include raw state, transaction/session/CSRF cookies, API keys, OIDC tokens, provider secrets,
prompt/completion content, or large logs.

## Commit and Push

Suggested commit:
`fix(identity): clear OIDC transaction cookie on all callback failures`

Commit and push all completed work to `origin/v2`.
Never push directly to main.
Do not ask whether to commit/push.

The task is complete only when all criteria above are green and origin/v2 contains the updated
handoff.

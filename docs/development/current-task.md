# AetherGate - Current Task

## Task ID
AGV2-030

## Title
Beta 1 security foundation - production provider-secret backend + egress DNS/redirect closure

## Active Branch

Work only on `feat/agv2-030-beta1-security`, created from canonical `main` commit
`7c47cd8575dc96265c02e17333ee88bda920de99`.

The historical persistent `v2` development branch is retired for new work.
Do not merge, rebase, or copy AGV2-030 work onto `v2`.
Never push task implementation directly to `main`.

## WIP Marker - FIRST LOCAL ACTION

Immediately after entering the repository, before pull/read/implementation work, create:

`.aethergate-wip`

Safe contents:
- task=AGV2-030
- branch=feat/agv2-030-beta1-security
- started_at=<UTC timestamp>

The marker is gitignored.
Never stage, commit, or push it.
Keep it present for the entire incomplete task.
If context is compacted/restarted and the task is incomplete, recreate it if missing.
If blocked/incomplete, leave it present.

Remove it only after:
1. every local/live criterion below is green;
2. all task commits are pushed to origin/feat/agv2-030-beta1-security;
3. the exact final origin/feat/agv2-030-beta1-security SHA has all four required GitHub Actions green;
4. the handoff is committed/pushed and remotely verified;
5. the feature PR to main exists and is updated.

## Starting Point

The complete core OpenAI surface is now merged to main:
- Models
- Chat Completions text + inline-image input
- Responses text + inline-image input subset
- Embeddings

Embeddings PR #3 is merged.

Canonical main merge commit:
`7c47cd8575dc96265c02e17333ee88bda920de99`

All four post-merge main workflows are completed/success on that exact SHA:
- backend: `38263020817`
- frontend: `38263020743`
- schema-secret-gate: `38263020802`
- alpha-golden-path: `38263020770`

Current green baseline:
- backend: **758 passed**
- frontend: **89 passed**
- Playwright: **32 passed / 6 skipped**
- migration head: **0017**
- Python OpenAI SDK: `openai==2.54.0`
- JavaScript OpenAI SDK: `openai@7.32.0`
- LiteLLM: `1.104.0`

This task begins Beta hardening. It is deliberately security-focused and does NOT declare Beta 1 complete.

## Why This Task Exists

Independent architectural review of the current canonical main found two explicit production-boundary
gaps that are already acknowledged in repository docs:

1. `src/aethergate/scheduler/runtime.py` unconditionally constructs `EnvSecretResolver()`, while
   `src/aethergate/secrets.py` and architecture docs explicitly say environment resolution is a
   dev/test convenience and NOT the production provider-secret backend.

2. `src/aethergate/egress.py` explicitly says redirect-following hardening is deferred. The current
   policy validates only the configured URL host; it does not close DNS rebinding at the actual
   connection boundary and does not prove a provider redirect cannot escape the allowed destination.

Both conflict with settled security requirements in `project_spec.md`:
- provider credentials live outside PostgreSQL behind a secret service/envelope boundary;
- metadata destinations, DNS rebinding, and redirect escapes are blocked.

AGV2-030 closes these production security gaps for the CURRENT supported provider path (Ollama /
pinned LiteLLM). Do not broaden to new provider kinds.

## 1. Compatibility / authority to read first

Before implementation read:
- `AGENTS.md`
- `project_spec.md`
- `docs/architecture/security.md`
- `docs/architecture/provider-model.md`
- `docs/operations/alpha-runbook.md`
- `src/aethergate/secrets.py`
- `src/aethergate/egress.py`
- `src/aethergate/scheduler/runtime.py`
- `src/aethergate/adapters/litellm.py`
- pinned LiteLLM `1.104.0` transport implementation actually used by `acompletion` and `aembedding`.

Do not guess redirect/DNS behavior from generic httpx defaults; inspect and prove the exact pinned
LiteLLM path.

## 2. Production provider-secret backend

Keep the `SecretResolver` protocol.

Add a production-suitable file-backed resolver intended for read-only secret projection from an
external orchestrator/secret manager, e.g. Docker Secrets, Kubernetes projected secrets, systemd
credentials, or an operator-managed read-only mount.

Recommended shape:
- `FileSecretResolver`
- runtime resolver factory such as `build_secret_resolver(settings)`.

### Configuration

Add typed settings with explicit environment aliases:

- `AETHERGATE_SECRET_BACKEND`
  - allowed: `env` | `file`
  - dev/test default may remain `env`
  - **prod MUST NOT use env**

- `AETHERGATE_SECRET_DIRECTORY`
  - required/validated when backend=file
  - use an absolute path
  - production documentation should use a read-only mounted directory, for example
    `/run/secrets/aethergate-provider`.

No implicit fallback:
- if prod is configured for file backend and the file is missing/invalid, fail closed;
- never check an environment variable as a backup;
- a canary secret present in `AETHERGATE_SECRET_*` must not rescue a missing file in prod.

### Stable file identity

Use the stable opaque `SecretRef.id` as the secret filename, not the human display name.

Reason:
- IDs are unique;
- display-name normalization can collide;
- user-controlled names must not become path traversal primitives.

Example:
`<secret_directory>/<secret_ref_id>`

Validate the path component and resolved path safely.

### File safety

At minimum:
- configured root must be absolute;
- reject path escape/traversal;
- candidate must resolve inside the configured root;
- regular files only;
- symlinks are permitted only when the resolved target remains inside the configured root
  (needed for common secret projections);
- reject symlink escape;
- bounded file size (choose/document a conservative limit, e.g. 64 KiB);
- UTF-8 text only;
- reject empty secret material;
- no NUL;
- operator-friendly terminal newline handling must be explicit and tested
  (removing one trailing LF/CRLF is acceptable if documented; never arbitrary whitespace stripping);
- errors contain only safe SecretRef metadata, never file contents.

Never return or log secret material except to the trusted adapter call that already consumes it.

## 3. Runtime resolver factory

Replace the production/runtime hard-coded `EnvSecretResolver()` in
`src/aethergate/scheduler/runtime.py` with the typed resolver factory.

Requirements:
- API and worker construct the same resolver policy;
- dev/test existing behavior remains easy;
- prod cannot instantiate EnvSecretResolver through normal runtime configuration;
- direct unit tests may instantiate EnvSecretResolver explicitly;
- dev-only utilities may remain explicit if clearly dev-only and tested;
- no secret resolver choice comes from a client request or database row.

Add tests proving API/worker runtime construction selects the expected resolver.

## 4. Config preflight

Extend `python -m aethergate.config_check` without printing secrets.

Prod:
- env provider-secret backend = INVALID;
- file backend with missing/non-absolute/unusable directory = INVALID;
- file backend correctly configured = VALID (subject to existing config rules).

Dev/test:
- env remains supported;
- file may also be selected for deterministic tests.

Preflight output may name the backend/directory state, but never file names containing sensitive
operator conventions if avoidable and never secret contents.

## 5. DNS rebinding closure - fail closed, do not overclaim

The current `DestinationPolicy` allowlists host strings and blocks literal metadata/link-local/etc.
That is not sufficient to claim DNS-rebinding resistance.

For CURRENT production Ollama support, choose ONE of these designs and prove it:

### Preferred if pinned LiteLLM supports a safe custom transport
Implement connection-level resolution/pinning such that:
- the exact resolved IP used for the connection is validated;
- DNS cannot change between validation and connect;
- HTTPS semantics/SNI remain correct where applicable;
- every address is checked against the destination policy.

### Acceptable narrow Beta policy if safe connection-level pinning is not cleanly available
Fail closed in `prod` by requiring the configured upstream `base_destination` host to be a literal
IPv4/IPv6 address.

This is acceptable for the current supported Ollama provider and the nomnom/private-LAN deployment.
It eliminates the DNS-rebinding class rather than pretending a pre-connect lookup is sufficient.

If this narrow policy is chosen:
- dev/test may continue to use service DNS names for deterministic Compose CI;
- docs must explicitly say production DNS upstream names remain disabled until transport-level
  pinning exists;
- do not add an insecure override such as "allow unsafe DNS" in prod.

In all environments preserve:
- explicit host allowlist;
- supported schemes only;
- no URL userinfo;
- no missing host;
- metadata/link-local/loopback/multicast/unspecified/reserved literal destinations;
- explicitly allow authorized private-LAN literal IPs when allowlisted.

Do not start rejecting RFC1918 private addresses generally: private Ollama is an intended deployment.

## 6. Redirect escape closure

Empirically inspect the pinned LiteLLM transport used by BOTH:
- `acompletion` (Chat / Responses / vision);
- `aembedding` (Embeddings).

A provider HTTP redirect MUST NOT be able to make the client contact a different destination.

Requirements:
- prove whether redirects are followed today;
- configure/replace the pinned client path so redirects are NOT followed, or enforce an equally strong
  per-hop policy;
- no hidden second request to the redirect target;
- 301/302/303/307/308 must fail safely as provider/upstream errors unless the transport returns a
  non-followed response that is safely mapped;
- never follow a redirect to loopback, metadata, link-local, a non-allowlisted host, or another
  private host;
- do not trust the `Location` header merely because the original host was allowed;
- redirect Location contents must not be reflected into logs/client errors.

### Required real-transport proof

Use a real local HTTP redirect fixture with the pinned LiteLLM transport, not only a monkeypatched
adapter method.

Create:
- allowed origin fixture;
- disallowed/canary target fixture that records hit count safely.

For completion AND embedding transports:
1. origin responds with each relevant redirect class;
2. Location points to canary target;
3. gateway/provider adapter returns safe failure;
4. canary hit count remains **zero**.

No network access outside the test environment.

## 7. Catalog + dispatch share the same policy

Endpoint create/update and dispatch-time route preparation must apply the same environment-aware
destination policy.

Prove:
- destination denied at catalog create/update is never persisted;
- if policy/config changes after a request was queued, dispatch-time validation still denies;
- prod hostname destination cannot slip through because it was persisted under dev/test rules;
- metadata/link-local literal destinations remain denied even if mistakenly present in the allowlist.

No direct DB-policy duplication in routers.

## 8. Provider-secret + egress interaction

Secret resolution occurs only AFTER the route/destination has passed the appropriate dispatch-time
security checks, or otherwise in an order that never exposes secret material to a denied destination.

Prove a denied destination does not receive provider credentials.

Do not log whether a particular secret value existed; safe "secret resolution failed" / "destination
denied" categories are sufficient.

## 9. Logging / diagnostics canaries

Extend redaction/security tests with:
- file-backed provider secret canary;
- redirect Location canary;
- secret directory/path canary where appropriate.

No logs/diagnostics/handoff may contain:
- provider secret contents;
- raw Authorization header;
- redirect Location if it contains sensitive query/userinfo;
- file contents;
- queue encryption key;
- OIDC/DB/bootstrap secrets.

Safe metadata:
- secret backend kind;
- SecretRef ID if existing policy already treats opaque IDs as safe;
- destination-denied reason family;
- HTTP status.

## 10. Deterministic offline CI

Extend existing deterministic tests/golden tooling without requiring Internet.

The normal offline golden path may continue in dev/test mode with service DNS and env fixture secrets.

Add explicit production-boundary tests that prove:
- prod + env SecretResolver configuration rejected;
- prod + file resolver works;
- no env fallback in prod;
- file path escape rejected;
- prod egress DNS policy is enforced;
- redirect canary never contacted by completion transport;
- redirect canary never contacted by embedding transport.

Do not weaken the existing fully offline CI requirement.

## 11. Live nomnom proof

Use nomnom for the real deployment check.

Do not download models.

Because the currently supported real provider is Ollama on the private LAN, verify using the actual
allowlisted literal-IP endpoint.

At minimum:
1. inspect current installed models/resources;
2. launch/recreate a disposable current AetherGate stack using the new production egress policy
   (or a production-policy test mode that exercises the identical policy object);
3. `bypass=false`;
4. real official Python SDK Models + one Chat request succeeds against the literal private IP;
5. existing vision request succeeds if the installed proven vision model is still available;
6. embeddings live proof only if an embedding-capable model is installed - do not download one;
7. hostname form of the same upstream is rejected in prod if the literal-IP policy was selected;
8. metadata/link-local target denied;
9. no unrelated services/resources touched;
10. tear down only disposable resources.

FileSecretResolver functionality may be proven deterministically if the real Ollama route has no
provider credential. Do not invent a fake live credential requirement merely to satisfy a checklist.

## 12. No migration

Expected: **NO migration**.

Migration head remains `0017`.
Do not modify migrations `0001`-`0017`.

Secret material remains outside PostgreSQL.
No new plaintext credential columns.

## 13. Documentation

Update:
- `docs/architecture/security.md`
- `docs/architecture/provider-model.md`
- `docs/architecture/v2-overview.md`
- `docs/operations/alpha-runbook.md` (or add a clearly named Beta security section)
- `docs/development/README.md`
- README if its security/deferred statements become stale
- `docs/development/agent-handoff.md`.

Remove stale statements saying:
- EnvSecretResolver is the only runtime resolver;
- production secret backend is wholly deferred;
- redirect hardening is still deferred,
but only after the implementation/proof exists.

Do NOT declare Beta 1 complete in this task.

Document the exact chosen production DNS policy honestly:
- transport-level pinning if actually implemented and proven; OR
- literal-IP-only production upstreams until pinning is implemented.

## 14. Regression gate

Run the complete current gate:

- backend full suite; baseline **758**
- frontend unit/component; baseline **89**
- full Playwright; baseline **32 passed / 6 skipped**
- npm build
- npm lint
- `npx tsc -b --noEmit`
- OpenAPI/client drift
- ruff check src tests
- git diff --check
- secret/content/image/vector/redirect canary scans
- no-legacy-v1 guard + self-test
- migration empty -> 0017
- populated 0016 -> 0017
- config_check dev valid
- config_check prod security cases
- fake Ollama Chat + Responses + vision + embeddings
- official Python SDK full supported surface
- official JS SDK full supported surface
- offline golden path
- new redirect real-transport tests.

Every red test must be root-caused.
Do not weaken tests to accept redirects or prod env secrets.

## 15. Feature PR + remote CI gate

Push completed work to `origin/feat/agv2-030-beta1-security`.

Create or update a PR from `feat/agv2-030-beta1-security` -> `main` (expected PR #4 if no other PR intervenes)
before the final remote-CI decision. The repository's current push filters historically name `main`/`v2`,
so task-branch CI may be supplied by the `pull_request` event. Do not wait forever for a branch-push run
that is not configured to exist.

On the EXACT FINAL PR HEAD SHA require all four workflows, from the PR event and/or any applicable push event:
- backend = completed/success
- frontend = completed/success
- schema-secret-gate = completed/success
- alpha-golden-path = completed/success.

The workflow run's `head_sha` must equal the exact final task-branch SHA.

If any is red:
- inspect logs;
- root-cause;
- fix;
- push;
- wait for the PR workflows on the new head;
- require all four green again on that exact new SHA.

Update the PR description with the final SHA, security design choice, verification summary, and exact run IDs.
Verify mergeable and leave it open/unmerged.

Do not declare completion from local evidence alone.
Do NOT merge automatically.
Never push directly to main.

## 16. No New Scope

Do not implement:
- production HA/failover;
- new provider kinds;
- Prometheus/OpenTelemetry exporters;
- paging/alerting;
- billing/invoice/payment;
- hosted SaaS tenancy;
- image generation/audio/tools/MCP/files;
- unrelated UI redesign;
- generic Vault/AWS/GCP SDK integrations.

The file resolver is the production boundary for externally projected provider secrets; do not add a
cloud-secret-manager matrix in this milestone.

## 17. Handoff

Record:
- implementation commits;
- exact provider-secret backend design;
- file naming/path safety/newline/size semantics;
- proof there is no prod env fallback;
- exact production DNS policy selected and why;
- pinned LiteLLM redirect behavior discovered;
- exact mechanism preventing redirect follow;
- completion + embedding redirect canary proof;
- catalog + dispatch policy proof;
- denied-destination/no-secret-exposure proof;
- logging/canary results;
- live nomnom result;
- migration head 0017;
- backend/frontend/Playwright counts;
- exact final task-branch SHA + four workflow IDs/conclusions;
- PR status/head;
- WIP lifecycle;
- exactly one recommended next step:

  **Begin AGV2-031 Beta 1 release hardening: production deployment profile, architecture/CODEOWNERS
  enforcement, fault-injection/recovery release gates, and Beta readiness documentation.**

Never include secret contents, file contents, credentials, provider/session tokens, prompt/output
content, encrypted payloads, redirect query contents, or large logs.

## Commit / Push

Use scoped conventional commits, for example:
- `feat(security): add production provider secret resolver`
- `fix(security): close upstream redirect and dns escape paths`
- `test(security): prove production inference boundaries`
- `docs: document beta security foundation`

Use fewer/more commits if cleaner.

Push all completed work to origin/feat/agv2-030-beta1-security.
Never push directly to main.
Do not ask whether to continue/commit/push.

The task is complete only when:
- production runtime cannot use env provider-secret resolution;
- file-backed projected provider secrets work without DB persistence or env fallback;
- DNS rebinding is actually closed for the supported production provider path (not merely
  pre-resolved and claimed safe);
- provider redirects cannot escape the configured destination for completion or embedding;
- catalog and dispatch enforce the same destination policy;
- denied destinations never receive provider credentials;
- existing Chat/Responses/vision/embeddings regressions remain green;
- migration head remains 0017;
- all four remote workflows are green on the exact final origin/feat/agv2-030-beta1-security SHA;
- a new `feat/agv2-030-beta1-security` -> `main` PR exists and remains unmerged;
- handoff is complete;
- `.aethergate-wip` is removed only after final remote verification.

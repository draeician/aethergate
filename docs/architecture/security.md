# AetherGate v2 — Security Model

Living document. Derived from the audit. Distinguish **settled** / **direction** / **deferred**.

## Identity and authentication

- OIDC-backed named identities; role/resource authorization; company MFA policy. (Settled)
- Scoped, expiring service-account authentication for automation. (Settled)
- Human Linux CLI login uses a supported OAuth device flow or equivalent approved flow. (Settled)
- Administrative and inference audiences/permissions are kept separate. (Settled)
- The inference data plane authenticates AetherGate-issued Bearer API keys (`agk_...`) and resolves
  them to a durable, typed request context (project / principal / credential / audience / scopes).
  (Settled — AGV2-012)

## Identity phase 1 — scoped inference API credentials (AGV2-012)

Machine/service authentication for the inference surface. Human OIDC login, browser sessions,
OAuth device flow, and full admin RBAC remain the next identity/admin phase.

- API keys are high-entropy random tokens (`agk_<display-prefix>_<secret>`, at least 256 bits of
  CSPRNG entropy) generated with Python `secrets`; the raw key is returned exactly once at
  create/rotate and is never stored. Only a SHA-256 verifier (`key_hash`) and the non-secret
  display prefix (`key_prefix`) are persisted. (Settled)
- SHA-256 verification is acceptable **only** because these are generated high-entropy tokens, not
  human-chosen passwords; this is not a password-hashing scheme and must not be reused for
  passwords. Exact lookup is by full-key hash (an indexed fixed-cost verifier); the display prefix
  is never the authentication selector. (Settled)
- Credentials carry an `audience` (`inference` | `admin`) and `scopes` (`inference:invoke` for
  phase 1). The inference endpoint rejects a wrong audience and any credential missing
  `inference:invoke`; a future admin credential is never valid for inference merely because it
  exists, and there is no shared master key. (Settled)
- A credential resolves only to an active project and active principal (principal belongs to the
  credential's project); inactive/expired/revoked/missing credential is denied with an
  indistinguishable authentication error. (Settled)
- Credential authorization is **revalidated immediately before scheduler dispatch**, not only at
  enqueue time, so revocation/expiry applied while a request is queued blocks upstream contact.
  (Settled)
- The development auth bypass (`AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS`) is a deliberate dev/test
  escape hatch only: rejected in `prod` mode, consulted only when no Authorization header is
  present, and never a fallback for an invalid supplied key. (Settled)

## Bootstrap and fail-closed startup

- A bootstrap credential is one-use, explicitly configured, and disabled after setup. (Settled)
- Management startup refuses absent, empty, or placeholder credentials; no shared default grants
  access. (Settled — closes AG-001.)

## Sessions and secrets

- Browser sessions are server-managed with Secure, HttpOnly cookies and CSRF protection. No
  long-lived master secret in JavaScript storage. (Settled)
- Upstream credentials are stored through a secrets service or envelope encryption with the
  decrypting key outside the database. (Settled)
- Secrets are returned only at explicit create/rotation boundaries, never in normal reads or exports. (Settled)
- Existing random client API keys may remain hash-verified; password hashing is not automatically
  required for high-entropy random tokens. (Direction)

## Authorization strictness

- Strict, standards-aware Bearer parsing; malformed and wrong-scheme headers fail predictably. (Settled — AG-020.)
- Cross-project resource access is denied consistently; revocation applies to queued work;
  administrative actions identify the actor. (Settled)

## Content and logging

- Content logging off by default; logs/traces redacted with defined retention. (Settled — AG-018.)
- SQL parameter logging disabled by default so prompt content and credentials do not leak. (Settled)
- Temporary queue payload storage is separate from permanent prompt logging, still encrypted,
  access-controlled, and expiring. (Settled)

## Egress control

- Enforce approved egress destinations; explicitly support authorized private LAN endpoints. (Settled)
- Block metadata destinations, DNS rebinding, and redirect escapes. (Settled)
- Inference callers never choose arbitrary server-side destinations, credentials, or trusted
  transport settings. (Settled — AG-033.)

## Transport

- TLS on management access, including LAN deployments; support a private management listener or
  ingress. (Settled)
- Honest identification to upstreams (no browser-impersonating User-Agent to bypass blocks). (Settled)

## Deferred

- Choice of secrets backend (KMS/HashiCorp Vault/envelope encryption library) — direction requires
  "secrets service or envelope encryption with keys outside the DB", specific tooling deferred.
- Exact retention/redaction schedule for personal information and prompt content.
- Whether password hashing is applied beyond high-entropy random tokens.

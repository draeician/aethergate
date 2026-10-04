"""API-key generation and one-way verification.

Generated credentials are high-entropy random tokens, so a one-way SHA-256
verifier is a safe verifier for this credential type. This is **not** a password
hashing scheme: SHA-256 offers no stretching or salting for low-entropy inputs,
so it must never be applied to human-chosen secrets. It is safe here only
because the raw key is 256 bits of CSPRNG entropy that cannot be feasibly
enumerated.

The raw key format is ``agk_<display>_<secret>`` where:

- ``agk`` is a recognizable, non-secret key-prefix marker;
- ``<display>`` is a short non-secret component used for display/debugging only
  (it contributes no meaningful secret entropy);
- ``<secret>`` carries the full 256 bits of secret entropy.

Only the SHA-256 hash of the full raw key and the safe ``agk_<display>`` prefix
are persisted. The raw key is returned exactly once at create/rotate time and
must never be logged, stored in PostgreSQL, or returned by read DTOs.
"""

from __future__ import annotations

import hashlib
import secrets

KEY_PREFIX = "agk"


def generate_api_key() -> tuple[str, str]:
    """Generate ``(raw_key, key_prefix)`` for a new credential.

    ``raw_key`` is the full Bearer token; ``key_prefix`` is the non-secret
    ``agk_<display>`` prefix safe to persist, display, and log.
    """
    secret = secrets.token_urlsafe(32)  # 32 bytes = 256 bits
    display = secrets.token_hex(4)  # 8 hex chars, display only
    raw = f"{KEY_PREFIX}_{display}_{secret}"
    prefix = f"{KEY_PREFIX}_{display}"
    return raw, prefix


def hash_raw_key(raw_key: str) -> str:
    """Return the SHA-256 hex digest of a raw API key (the persisted verifier)."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


__all__ = ["KEY_PREFIX", "generate_api_key", "hash_raw_key"]

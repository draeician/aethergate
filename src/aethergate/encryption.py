"""Application-boundary queue-content encryption.

Scheduler payloads (prompts, completions, and streamed content) are encrypted
before they touch PostgreSQL, using authenticated encryption (Fernet/AES-GCM
from the ``cryptography`` library) with a key that lives outside the database.

The key is provided via ``AETHERGATE_QUEUE_KEY`` and must never be committed or
logged. Production fails closed (``QueueKeyError``) when the key is absent or
invalid. Key rotation is deferred.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from aethergate.errors import QueueKeyError


class QueueEncryptor:
    """Authenticated encrypt/decrypt for scheduler payloads."""

    def __init__(self, key: str) -> None:
        if not isinstance(key, str) or not key.strip():
            raise QueueKeyError("queue encryption key is empty")
        try:
            self._fernet = Fernet(key.encode("ascii"))
        except (ValueError, Exception) as exc:  # noqa: BLE001 - any Fernet parse failure
            raise QueueKeyError("queue encryption key is not a valid Fernet key") from exc

    def encrypt(self, data: bytes) -> bytes:
        return self._fernet.encrypt(data)

    def decrypt(self, token: bytes) -> bytes:
        try:
            return self._fernet.decrypt(token)
        except InvalidToken as exc:
            raise QueueKeyError("queue payload could not be decrypted") from exc

    @staticmethod
    def generate_key() -> str:
        return Fernet.generate_key().decode("ascii")


def encryptor_from_key(key: str | None) -> QueueEncryptor:
    """Build a :class:`QueueEncryptor`, failing closed if ``key`` is absent."""
    if key is None:
        raise QueueKeyError("queue encryption key is not configured")
    return QueueEncryptor(key)

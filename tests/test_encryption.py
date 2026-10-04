"""Queue-content encryption tests (offline)."""

from __future__ import annotations

import pytest

from aethergate.encryption import QueueEncryptor, encryptor_from_key
from aethergate.errors import QueueKeyError


def test_round_trip():
    encryptor = QueueEncryptor(QueueEncryptor.generate_key())
    token = encryptor.encrypt(b"canary-prompt-do-not-leak")
    assert b"canary" not in token
    assert encryptor.decrypt(token) == b"canary-prompt-do-not-leak"


def test_distinct_keys_produce_distinct_ciphertext():
    a = QueueEncryptor(QueueEncryptor.generate_key())
    b = QueueEncryptor(QueueEncryptor.generate_key())
    assert a.encrypt(b"same") != b.encrypt(b"same")


def test_wrong_key_cannot_decrypt():
    a = QueueEncryptor(QueueEncryptor.generate_key())
    b = QueueEncryptor(QueueEncryptor.generate_key())
    token = a.encrypt(b"secret")
    with pytest.raises(QueueKeyError):
        b.decrypt(token)


def test_tampered_token_rejected():
    encryptor = QueueEncryptor(QueueEncryptor.generate_key())
    token = bytearray(encryptor.encrypt(b"secret"))
    token[-1] ^= 0xFF
    with pytest.raises(QueueKeyError):
        encryptor.decrypt(bytes(token))


def test_empty_key_rejected():
    with pytest.raises(QueueKeyError):
        QueueEncryptor("")


def test_invalid_key_rejected():
    with pytest.raises(QueueKeyError):
        QueueEncryptor("not-a-valid-fernet-key")


def test_encryptor_from_key_requires_key():
    with pytest.raises(QueueKeyError):
        encryptor_from_key(None)

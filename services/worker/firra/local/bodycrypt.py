"""Authenticated row-bound body encryption; keys live only in the app's memory slots."""

import base64
import binascii
import os
import threading
from dataclasses import dataclass, field
from weakref import WeakKeyDictionary

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from firra.local.secrets import SecretStore

PREFIX = "enc:v1:"


class BodyCryptError(Exception):
    """Invalid key, format, version, authentication or plaintext encoding; no data attached."""


def encrypt(key: bytes, plaintext: str, aad: str) -> str:
    try:
        if len(key) != 32:
            raise ValueError
        nonce = os.urandom(12)
        sealed = AESGCM(key).encrypt(nonce, plaintext.encode(), aad.encode())
        return PREFIX + base64.b64encode(nonce + sealed).decode("ascii")
    except (ValueError, UnicodeError):
        raise BodyCryptError("body_encrypt_failed") from None


def decrypt(key: bytes, token: str, aad: str) -> str:
    try:
        if len(key) != 32 or not token.startswith(PREFIX):
            raise ValueError
        sealed = base64.b64decode(token[len(PREFIX) :], validate=True)
        if len(sealed) < 28:
            raise ValueError
        return AESGCM(key).decrypt(sealed[:12], sealed[12:], aad.encode()).decode()
    except (ValueError, binascii.Error, InvalidTag, UnicodeError):
        raise BodyCryptError("body_decrypt_failed") from None


@dataclass(frozen=True)
class BodyKeyProvider:
    secrets: SecretStore = field(repr=False)
    allow_plaintext_migration: bool = False

    def key(self) -> bytes:
        value = self.secrets.require("db_encryption_key")
        try:
            key = base64.b64decode(value, validate=True)
            if len(key) != 32:
                raise ValueError
            return key
        except (ValueError, binascii.Error):
            raise BodyCryptError("body_key_invalid") from None


# Bound per pool so unchanged handlers instantiate repositories without carrying keys.
# Weak ownership prevents an old profile/key leaking into a new database session.
_providers: WeakKeyDictionary[object, BodyKeyProvider] = WeakKeyDictionary()
_lock = threading.Lock()


def bind(pool: object, provider: BodyKeyProvider) -> None:
    provider.key()  # Fail before any repository can write unencrypted local data.
    with _lock:
        _providers[pool] = provider


def provider_for(pool: object) -> BodyKeyProvider | None:
    with _lock:
        return _providers.get(pool)


def write_body(
    pool: object, workspace_id: object, record_id: object, body: str | None
) -> str | None:
    provider = provider_for(pool)
    if body is None or provider is None:
        return body  # Existing cloud profile remains unchanged until F9.
    return encrypt(provider.key(), body, f"{workspace_id}:{record_id}")


def read_body(
    pool: object, workspace_id: object, record_id: object, body: str | None
) -> str | None:
    provider = provider_for(pool)
    if body is None or provider is None:
        return body
    if not body.startswith("enc:") and provider.allow_plaintext_migration:
        return body
    return decrypt(provider.key(), body, f"{workspace_id}:{record_id}")


# Content fields kept in ``source_record.metadata`` (mail subject, meeting title) are encrypted like
# bodies; the field name is part of the associated data so a value cannot be moved between fields.
ENCRYPTED_FIELDS = ("subject", "title")


def write_field(
    pool: object, workspace_id: object, record_id: object, field: str, value: str | None
) -> str | None:
    provider = provider_for(pool)
    if value is None or provider is None:
        return value
    return encrypt(provider.key(), value, f"{workspace_id}:{record_id}:{field}")


def read_field(
    pool: object, workspace_id: object, record_id: object, field: str, value: str | None
) -> str | None:
    """Plaintext values from before the engine encrypted fields are returned as they are; the
    engine re-encrypts them at start (``firra.db.local_secure``)."""
    provider = provider_for(pool)
    if value is None or provider is None or not value.startswith(PREFIX):
        return value
    return decrypt(provider.key(), value, f"{workspace_id}:{record_id}:{field}")

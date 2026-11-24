"""Backup envelope: scrypt from a recovery phrase, AES-256-GCM over the JSON payload.

The header (version and KDF parameters) is the GCM associated data, so changing a parameter
breaks the tag. A wrong phrase and a tampered file are indistinguishable: both raise
``BackupDecryptError``.
"""

import base64
import json
import os
import secrets
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

VERSION = 1
_N, _R, _P = 2**15, 8, 1


class BackupDecryptError(Exception):
    """Wrong recovery phrase, or the file was changed or is not a Firra backup."""


def new_recovery_phrase() -> str:
    """120 random bits as ``XXXX-XXXX-...`` (base32): shown once, never stored by Firra."""
    raw = base64.b32encode(secrets.token_bytes(15)).decode()
    return "-".join(raw[i : i + 4] for i in range(0, len(raw), 4))


def _key(phrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    normalized = phrase.replace("-", "").replace(" ", "").upper().encode()
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(normalized)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def encrypt(payload: dict[str, Any], phrase: str) -> bytes:
    salt, nonce = os.urandom(16), os.urandom(12)
    header = {"v": VERSION, "kdf": "scrypt", "n": _N, "r": _R, "p": _P, "salt": _b64(salt)}
    aad = json.dumps(header, sort_keys=True).encode()
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    sealed = AESGCM(_key(phrase, salt, _N, _R, _P)).encrypt(nonce, body, aad)
    return json.dumps({**header, "nonce": _b64(nonce), "ct": _b64(sealed)}).encode()


def decrypt(blob: bytes, phrase: str) -> dict[str, Any]:
    try:
        envelope = json.loads(blob)
        header = {k: envelope[k] for k in ("v", "kdf", "n", "r", "p", "salt")}
        if header["v"] != VERSION or header["kdf"] != "scrypt":
            raise BackupDecryptError
        # Bound the work a file can ask for before running the KDF.
        if header["n"] > 2**17 or header["r"] > 16 or header["p"] > 4:
            raise BackupDecryptError
        key = _key(phrase, base64.b64decode(header["salt"]), header["n"], header["r"], header["p"])
        aad = json.dumps(header, sort_keys=True).encode()
        body = AESGCM(key).decrypt(
            base64.b64decode(envelope["nonce"]), base64.b64decode(envelope["ct"]), aad
        )
        result = json.loads(body)
    except BackupDecryptError:
        raise
    except (InvalidTag, ValueError, KeyError, TypeError):
        raise BackupDecryptError from None
    if not isinstance(result, dict):
        raise BackupDecryptError
    return result

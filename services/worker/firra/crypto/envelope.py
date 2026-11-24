"""AES-256-GCM envelope format shared with apps/api (src/crypto/envelope.ts).

Ciphertext: ``v1.<base64url(iv)>.<base64url(ciphertext || tag)>`` with 12-byte IV and 16-byte tag.
The associated data binds a ciphertext to its row (``<workspace_id>:<connection_id>``), so a
token copied into another row fails to decrypt.
"""

import base64
import os
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION: Final = "v1"
DEK_BYTES: Final = 32
IV_BYTES: Final = 12


class DecryptionError(Exception):
    """Ciphertext is malformed, tampered with, or bound to other associated data."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def new_dek() -> bytes:
    return os.urandom(DEK_BYTES)


def encrypt(dek: bytes, plaintext: str, aad: str, *, iv: bytes | None = None) -> str:
    iv = iv if iv is not None else os.urandom(IV_BYTES)
    if len(dek) != DEK_BYTES or len(iv) != IV_BYTES:
        raise ValueError("invalid key or iv length")
    sealed = AESGCM(dek).encrypt(iv, plaintext.encode("utf-8"), aad.encode("utf-8"))
    return f"{VERSION}.{_b64(iv)}.{_b64(sealed)}"


def decrypt(dek: bytes, ciphertext: str, aad: str) -> str:
    try:
        version, iv_text, sealed_text = ciphertext.split(".")
        if version != VERSION:
            raise DecryptionError("unsupported version")
        plaintext = AESGCM(dek).decrypt(_unb64(iv_text), _unb64(sealed_text), aad.encode("utf-8"))
    except (ValueError, InvalidTag) as exc:
        raise DecryptionError("cannot decrypt") from exc
    return plaintext.decode("utf-8")


def token_aad(workspace_id: object, connection_id: object) -> str:
    return f"{workspace_id}:{connection_id}"

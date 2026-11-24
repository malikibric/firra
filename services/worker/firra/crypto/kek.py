"""Key-encryption keys: Cloud KMS in deployed environments, a static local key for local/test."""

import base64
import time
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from firra.crypto import envelope

_METADATA_SERVICE_ACCOUNT_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
)


class Kek(Protocol):
    @property
    def name(self) -> str: ...

    def wrap(self, dek: bytes) -> bytes: ...

    def unwrap(self, wrapped: bytes) -> bytes: ...


@dataclass(frozen=True)
class LocalKek:
    """AES-GCM wrapping with a static key from settings. Refused outside local/test by Settings."""

    key: bytes
    name: str = "local"

    def wrap(self, dek: bytes) -> bytes:
        return envelope.encrypt(self.key, base64.b64encode(dek).decode(), "kek").encode()

    def unwrap(self, wrapped: bytes) -> bytes:
        return base64.b64decode(envelope.decrypt(self.key, wrapped.decode(), "kek"))


@dataclass
class MetadataTokenSource:
    """Access tokens for the Cloud Run service account from the metadata server."""

    client: httpx.Client
    _token: str = field(default="", init=False)
    _expires_at: float = field(default=0.0, init=False)

    def token(self) -> str:
        if time.monotonic() > self._expires_at - 60:
            response = self.client.get(
                _METADATA_SERVICE_ACCOUNT_URL, headers={"Metadata-Flavor": "Google"}
            )
            response.raise_for_status()
            body = response.json()
            self._token = str(body["access_token"])
            self._expires_at = time.monotonic() + float(body["expires_in"])
        return self._token


@dataclass
class CloudKmsKek:
    """Wraps DEKs with Cloud KMS (`projects/…/cryptoKeys/token-kek`) through the REST API."""

    name: str
    client: httpx.Client
    tokens: MetadataTokenSource

    def _call(self, method: str, payload: dict[str, str]) -> dict[str, str]:
        response = self.client.post(
            f"https://cloudkms.googleapis.com/v1/{self.name}:{method}",
            json=payload,
            headers={"Authorization": f"Bearer {self.tokens.token()}"},
        )
        response.raise_for_status()
        body: dict[str, str] = response.json()
        return body

    def wrap(self, dek: bytes) -> bytes:
        body = self._call("encrypt", {"plaintext": base64.b64encode(dek).decode()})
        return base64.b64decode(body["ciphertext"])

    def unwrap(self, wrapped: bytes) -> bytes:
        body = self._call("decrypt", {"ciphertext": base64.b64encode(wrapped).decode()})
        return base64.b64decode(body["plaintext"])

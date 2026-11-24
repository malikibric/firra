"""Raw object store: GCS in deployed environments, a local directory for local/test.

Object names contain ids only (never subjects, filenames from mail, or addresses).
Docs checked: https://docs.cloud.google.com/storage/docs/json_api/v1/objects/delete (2026-10-02).
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import httpx

from firra.crypto.kek import MetadataTokenSource


class RawStore(Protocol):
    def put(self, name: str, data: bytes, content_type: str) -> str | None:
        """Stores an object and returns its URI, or None when this store keeps nothing."""
        ...

    def get(self, uri: str) -> bytes: ...

    def delete(self, uri: str) -> None:
        """Deletes an object; an already missing object is success."""
        ...


class ObjectNotFoundError(Exception):
    pass


def _safe_name(name: str) -> str:
    if not name or name.startswith("/") or ".." in name.split("/"):
        raise ValueError("invalid object name")
    return name


@dataclass
class GcsRawStore:
    bucket: str
    client: httpx.Client
    tokens: MetadataTokenSource

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens.token()}"}

    def put(self, name: str, data: bytes, content_type: str) -> str:
        response = self.client.post(
            f"https://storage.googleapis.com/upload/storage/v1/b/{self.bucket}/o",
            params={"uploadType": "media", "name": _safe_name(name)},
            content=data,
            headers={**self._auth(), "Content-Type": content_type},
        )
        response.raise_for_status()
        return f"gs://{self.bucket}/{name}"

    def get(self, uri: str) -> bytes:
        prefix = f"gs://{self.bucket}/"
        if not uri.startswith(prefix):
            raise ValueError("object is not in this bucket")
        name = _safe_name(uri.removeprefix(prefix))
        response = self.client.get(
            f"https://storage.googleapis.com/storage/v1/b/{self.bucket}/o/{quote(name, safe='')}",
            params={"alt": "media"},
            headers=self._auth(),
        )
        if response.status_code == 404:
            raise ObjectNotFoundError(uri)
        response.raise_for_status()
        return response.content

    def delete(self, uri: str) -> None:
        prefix = f"gs://{self.bucket}/"
        if not uri.startswith(prefix):
            raise ValueError("object is not in this bucket")
        name = _safe_name(uri.removeprefix(prefix))
        response = self.client.delete(
            f"https://storage.googleapis.com/storage/v1/b/{self.bucket}/o/{quote(name, safe='')}",
            headers=self._auth(),
        )
        if response.status_code != 404:
            response.raise_for_status()


@dataclass
class LocalRawStore:
    root: Path
    bucket: str = "local"

    def _path(self, name: str) -> Path:
        path = (self.root / _safe_name(name)).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError("invalid object name")
        return path

    def put(self, name: str, data: bytes, content_type: str) -> str:
        path = self._path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return f"gs://{self.bucket}/{name}"

    def get(self, uri: str) -> bytes:
        prefix = f"gs://{self.bucket}/"
        if not uri.startswith(prefix):
            raise ValueError("object is not in this bucket")
        path = self._path(uri.removeprefix(prefix))
        if not path.exists():
            raise ObjectNotFoundError(uri)
        return path.read_bytes()

    def delete(self, uri: str) -> None:
        prefix = f"gs://{self.bucket}/"
        if not uri.startswith(prefix):
            raise ValueError("object is not in this bucket")
        self._path(uri.removeprefix(prefix)).unlink(missing_ok=True)


class NoRawStore:
    """The local profile keeps no raw provider payloads: the encrypted ``body_clean`` is the copy.

    Raw messages hold the whole uncleaned body, every address and attachments; writing them to
    disk would bypass the database encryption (``docs/plans/local/source-layer.md`` S0).
    """

    def put(self, name: str, data: bytes, content_type: str) -> None:
        return None

    def get(self, uri: str) -> bytes:
        raise ObjectNotFoundError(uri)

    def delete(self, uri: str) -> None:
        return None

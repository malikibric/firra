from pathlib import Path

import httpx
import pytest

from firra.crypto.kek import MetadataTokenSource
from firra.storage.raw import GcsRawStore, LocalRawStore, ObjectNotFoundError


def test_local_store_roundtrip_and_rejects_traversal(tmp_path: Path) -> None:
    store = LocalRawStore(tmp_path)
    uri = store.put("raw/ws/conn/abc.json", b"{}", "application/json")
    assert uri == "gs://local/raw/ws/conn/abc.json"
    assert store.get(uri) == b"{}"
    for bad in ("../escape", "/abs", "raw/../../x"):
        with pytest.raises(ValueError):
            store.put(bad, b"x", "text/plain")
    with pytest.raises(ObjectNotFoundError):
        store.get("gs://local/raw/missing.json")
    with pytest.raises(ValueError):
        store.get("gs://other-bucket/raw/ws/conn/abc.json")


def test_gcs_store_uses_json_api() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "metadata.google.internal":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer t"
        seen.append(f"{request.method} {request.url}")
        if request.method == "POST":
            return httpx.Response(200, json={})
        return httpx.Response(200, content=b"payload")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    store = GcsRawStore("firra-raw", client, MetadataTokenSource(client))
    uri = store.put("raw/ws/conn/a.json", b"{}", "application/json")
    assert uri == "gs://firra-raw/raw/ws/conn/a.json"
    assert store.get(uri) == b"payload"
    assert seen[0].startswith(
        "POST https://storage.googleapis.com/upload/storage/v1/b/firra-raw/o?"
    )
    assert "raw%2Fws%2Fconn%2Fa.json?alt=media" in seen[1]


def test_local_delete_is_idempotent_and_confined(tmp_path: Path) -> None:
    store = LocalRawStore(tmp_path)
    uri = store.put("raw/a.json", b"payload", "application/json")
    store.delete(uri)
    store.delete(uri)
    with pytest.raises(ObjectNotFoundError):
        store.get(uri)
    for bad in ("gs://other/raw/a", "gs://local/../escape", "gs://local//absolute"):
        with pytest.raises(ValueError):
            store.delete(bad)


@pytest.mark.parametrize("status", [204, 404, 403, 500])
def test_gcs_delete_missing_is_success_other_errors_propagate(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "metadata.google.internal":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        assert request.method == "DELETE"
        assert request.url.raw_path.endswith(b"raw%2Fa.json")
        assert request.headers["Authorization"] == "Bearer t"
        return httpx.Response(status)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        store = GcsRawStore("raw", client, MetadataTokenSource(client))
        if status in (204, 404):
            store.delete("gs://raw/raw/a.json")
        else:
            with pytest.raises(httpx.HTTPStatusError):
                store.delete("gs://raw/raw/a.json")
        with pytest.raises(ValueError):
            store.delete("gs://foreign/raw/a.json")

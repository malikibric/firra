import base64
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from firra.crypto import envelope
from firra.crypto.kek import CloudKmsKek, LocalKek, MetadataTokenSource
from firra.crypto.keyring import Keyring
from firra.db.types import WorkspaceId

VECTORS = json.loads(
    (Path(__file__).parents[4] / "packages/contracts/fixtures/crypto_vectors.json").read_text()
)


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


DEK = unb64(VECTORS["dek"])


@pytest.mark.parametrize("vector", VECTORS["vectors"])
def test_shared_vectors(vector: dict[str, str]) -> None:
    # Same vectors are checked by apps/api, so both languages agree on the format.
    assert envelope.decrypt(DEK, vector["ciphertext"], vector["aad"]) == vector["plaintext"]
    iv = unb64(vector["iv"])
    assert envelope.encrypt(DEK, vector["plaintext"], vector["aad"], iv=iv) == vector["ciphertext"]


def test_roundtrip_uses_fresh_iv() -> None:
    dek = envelope.new_dek()
    a = envelope.encrypt(dek, "secret", "ws:conn")
    b = envelope.encrypt(dek, "secret", "ws:conn")
    assert a != b
    assert envelope.decrypt(dek, a, "ws:conn") == "secret"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.replace("v1.", "v2.", 1),
        lambda c: c[:-2] + ("AA" if not c.endswith("AA") else "BB"),
        lambda c: c.split(".")[0],
        lambda c: "garbage",
    ],
)
def test_tampering_is_detected(mutate: object) -> None:
    dek = envelope.new_dek()
    blob = envelope.encrypt(dek, "secret", "ws:conn")
    with pytest.raises(envelope.DecryptionError):
        envelope.decrypt(dek, mutate(blob), "ws:conn")  # type: ignore[operator]


def test_ciphertext_is_bound_to_its_row() -> None:
    dek = envelope.new_dek()
    blob = envelope.encrypt(dek, "secret", envelope.token_aad("ws", "conn-a"))
    with pytest.raises(envelope.DecryptionError):
        envelope.decrypt(dek, blob, envelope.token_aad("ws", "conn-b"))


class MemoryKeyStore:
    def __init__(self) -> None:
        self.rows: dict[WorkspaceId, bytes] = {}
        self.inserts = 0

    def get(self, *, workspace_id: WorkspaceId) -> bytes | None:
        return self.rows.get(workspace_id)

    def insert_if_absent(
        self, *, workspace_id: WorkspaceId, wrapped_dek: bytes, kek_name: str
    ) -> bytes:
        self.inserts += 1
        return self.rows.setdefault(workspace_id, wrapped_dek)


def test_keyring_creates_one_key_per_workspace_and_encrypts_tokens() -> None:
    store = MemoryKeyStore()
    keyring = Keyring(kek=LocalKek(bytes(32)), store=store)
    ws_a, ws_b = WorkspaceId(uuid4()), WorkspaceId(uuid4())
    conn = uuid4()
    blob = keyring.encrypt_token(ws_a, conn, '{"refresh_token":"x"}')
    assert keyring.decrypt_token(ws_a, conn, blob) == '{"refresh_token":"x"}'
    assert store.inserts == 1
    keyring.dek(ws_a)
    assert store.inserts == 1, "DEK is cached"
    assert keyring.dek(ws_a) != keyring.dek(ws_b)

    # A fresh process unwraps the stored key and can still decrypt.
    other = Keyring(kek=LocalKek(bytes(32)), store=store)
    assert other.decrypt_token(ws_a, conn, blob) == '{"refresh_token":"x"}'
    with pytest.raises(envelope.DecryptionError):
        other.decrypt_token(ws_b, conn, blob)


def test_cloud_kms_kek_calls_rest_api() -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "metadata.google.internal":
            assert request.headers["Metadata-Flavor"] == "Google"
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer tok"
        body = json.loads(request.content)
        calls.append((request.url.path, body))
        # Fake KMS: "wrapping" prefixes the key bytes so the round-trip is checkable.
        if request.url.path.endswith(":encrypt"):
            wrapped = b"kms:" + base64.b64decode(body["plaintext"])
            return httpx.Response(200, json={"ciphertext": base64.b64encode(wrapped).decode()})
        unwrapped = base64.b64decode(body["ciphertext"]).removeprefix(b"kms:")
        return httpx.Response(200, json={"plaintext": base64.b64encode(unwrapped).decode()})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    name = "projects/p/locations/europe-west3/keyRings/firra/cryptoKeys/token-kek"
    kek = CloudKmsKek(name=name, client=client, tokens=MetadataTokenSource(client))
    dek = envelope.new_dek()
    assert kek.unwrap(kek.wrap(dek)) == dek
    assert calls[0][0] == f"/v1/{name}:encrypt"
    assert calls[1][0] == f"/v1/{name}:decrypt"

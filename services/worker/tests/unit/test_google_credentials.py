import json

import httpx
import pytest

from firra.connectors.errors import TokenRevokedError
from firra.connectors.google.credentials import (
    GoogleDelegatedTokens,
    GoogleUserTokens,
    StoredGoogleTokens,
)
from firra.crypto.kek import MetadataTokenSource
from tests.fake_google import FakeGoogle


def test_user_refresh_token_exchange_and_revocation() -> None:
    fake = FakeGoogle(revoked_refresh_tokens={"revoked"})
    tokens = GoogleUserTokens("cid", "secret", fake.client())
    assert tokens.access_token(StoredGoogleTokens("good")) == "access-ok"
    with pytest.raises(TokenRevokedError):
        tokens.access_token(StoredGoogleTokens("revoked"))


def test_delegation_signs_with_iam_credentials_and_exchanges_the_assertion() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "metadata.google.internal":
            return httpx.Response(200, json={"access_token": "sa-token", "expires_in": 3600})
        if request.url.host == "iamcredentials.googleapis.com":
            assert request.headers["Authorization"] == "Bearer sa-token"
            seen["path"] = request.url.path
            seen["claims"] = json.loads(json.loads(request.content)["payload"])
            return httpx.Response(200, json={"signedJwt": "signed"})
        form = request.content.decode()
        assert "grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer" in form
        assert "assertion=signed" in form
        return httpx.Response(200, json={"access_token": "delegated-ok"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    tokens = GoogleDelegatedTokens(
        "worker@p.iam.gserviceaccount.com", client, MetadataTokenSource(client)
    )
    assert tokens.access_token("ana@northbeam.agency", ["scope-a", "scope-b"]) == "delegated-ok"
    assert seen["path"] == "/v1/projects/-/serviceAccounts/worker@p.iam.gserviceaccount.com:signJwt"
    claims = seen["claims"]
    assert isinstance(claims, dict)
    assert claims["sub"] == "ana@northbeam.agency" and claims["scope"] == "scope-a scope-b"

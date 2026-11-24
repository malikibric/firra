import json

import httpx
import pytest

from firra.connectors.errors import TokenRevokedError
from firra.connectors.slack import auth
from firra.http import ProviderError


def client(
    status: int = 200, body: dict[str, object] | None = None, scopes: tuple[str, ...] | None = None
) -> httpx.Client:
    scope_header = ",".join(auth.READ_SCOPES if scopes is None else scopes)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"].startswith("Bearer xoxp-")
        return httpx.Response(
            status,
            json=body
            if body is not None
            else {"ok": True, "team_id": "T1", "team": "Acme", "user_id": "U1"},
            headers={"x-oauth-scopes": scope_header},
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_manifest_is_read_only_user_scopes_only() -> None:
    m = auth.manifest()
    assert m["oauth_config"]["scopes"] == {"user": list(auth.READ_SCOPES)}  # type: ignore[index]
    assert "bot" not in m["oauth_config"]["scopes"]  # type: ignore[index]
    settings = m["settings"]
    assert settings["socket_mode_enabled"] is False and settings["org_deploy_enabled"] is False  # type: ignore[index]
    assert not any(s.startswith(("chat:", "files:write", "admin")) for s in auth.READ_SCOPES)
    url = auth.creation_url()
    assert url.startswith("https://api.slack.com/apps?new_app=1&manifest_json=%7B")
    assert json.loads(auth.manifest_json()) == m


def test_valid_token_returns_the_workspace_and_user() -> None:
    identity = auth.validate_token(client(), "xoxp-1-secret")
    assert (identity.team_id, identity.team_name, identity.user_id) == ("T1", "Acme", "U1")
    assert identity.scopes == frozenset(auth.READ_SCOPES)


@pytest.mark.parametrize("token", ["xoxb-bot", "xapp-1", "", "random"])
def test_only_user_tokens_are_accepted(token: str) -> None:
    with pytest.raises(auth.SlackTokenRefusedError) as exc:
        auth.validate_token(client(), token)
    assert exc.value.code == "not_a_user_token"


def test_scopes_must_be_exactly_the_read_scopes() -> None:
    with pytest.raises(auth.SlackTokenRefusedError) as missing:
        auth.validate_token(client(scopes=auth.READ_SCOPES[:-1]), "xoxp-1")
    assert missing.value.code == "missing_scopes"
    with pytest.raises(auth.SlackTokenRefusedError) as broad:
        auth.validate_token(client(scopes=(*auth.READ_SCOPES, "chat:write")), "xoxp-1")
    assert broad.value.code == "scopes_too_broad"


def test_slack_errors_become_safe_codes() -> None:
    with pytest.raises(auth.SlackTokenRefusedError) as bad:
        auth.validate_token(client(body={"ok": False, "error": "invalid_auth"}), "xoxp-1")
    assert bad.value.code == "token_invalid"
    with pytest.raises(auth.SlackTokenRefusedError) as down:
        auth.validate_token(client(status=503, body={}), "xoxp-1")
    assert down.value.code == "slack_unavailable"
    with pytest.raises(auth.SlackTokenRefusedError) as other:
        auth.validate_token(
            client(body={"ok": False, "error": "ratelimited_or_whatever text"}), "xoxp-1"
        )
    assert other.value.code == "slack_refused"
    assert "whatever" not in str(other.value)


def test_check_response_maps_token_and_provider_errors() -> None:
    auth.check_response({"ok": True})
    with pytest.raises(TokenRevokedError):
        auth.check_response({"ok": False, "error": "token_revoked"})
    with pytest.raises(ProviderError) as exc:
        auth.check_response({"ok": False, "error": "channel_not_found"})
    assert exc.value.reason == "channel_not_found"


def test_revoke_never_raises() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    auth.revoke(httpx.Client(transport=httpx.MockTransport(boom)), "xoxp-1")

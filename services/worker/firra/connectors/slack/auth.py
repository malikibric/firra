"""Slack: the manifest for the owner's own Slack app, and validation of the token pasted back.

Docs checked (docs/research/slack-local.md, 2026-10-05): app creation from a manifest
(``https://api.slack.com/apps?new_app=1&manifest_json=...``), user-token read scopes,
``auth.test``, ``auth.revoke``. Firra ships **no Slack app of its own**: a distributed app outside
the Marketplace is rate limited to 1 request per minute, an internal app created by the user keeps
Tier 3. The token is a *user* token (``xoxp-``) with read scopes only; a token with any write
scope is refused.
"""

import json
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from firra.connectors.errors import TokenRevokedError
from firra.http import ProviderError

API = "https://slack.com/api"
# What Firra reads: history of public, private, direct and group-direct conversations, the
# conversation and user lists, names and emails (external Slack Connect users often have none).
READ_SCOPES = (
    "channels:history",
    "groups:history",
    "im:history",
    "mpim:history",
    "channels:read",
    "groups:read",
    "im:read",
    "mpim:read",
    "users:read",
    "users:read.email",
)
REQUIRED_SCOPES = frozenset(READ_SCOPES)
APP_NAME = "Firra (read-only)"


def manifest() -> dict[str, object]:
    """The app manifest: user scopes only, no bot, no events, no sockets, no write access."""
    return {
        "display_information": {
            "name": APP_NAME,
            "description": "Lets Firra on your Mac read the channels you map to your clients.",
        },
        "oauth_config": {"scopes": {"user": list(READ_SCOPES)}},
        "settings": {
            "org_deploy_enabled": False,
            "socket_mode_enabled": False,
            "token_rotation_enabled": False,
        },
    }


def manifest_json() -> str:
    return json.dumps(manifest(), separators=(",", ":"), sort_keys=True)


def creation_url() -> str:
    """A link that opens Slack with the manifest prefilled (pasting the manifest works too)."""
    return f"https://api.slack.com/apps?new_app=1&manifest_json={quote(manifest_json())}"


@dataclass(frozen=True)
class SlackIdentity:
    team_id: str
    team_name: str
    user_id: str
    scopes: frozenset[str]


class SlackTokenRefusedError(Exception):
    """The pasted token cannot be used; ``code`` is a class name that is safe to show."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def validate_token(client: httpx.Client, token: str) -> SlackIdentity:
    """``auth.test`` plus scope checks. Raises ``SlackTokenRefusedError`` with a safe code."""
    if not token.startswith("xoxp-"):
        raise SlackTokenRefusedError("not_a_user_token")
    try:
        response = client.post(f"{API}/auth.test", headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError:
        raise SlackTokenRefusedError("slack_unreachable") from None
    if response.status_code >= 500 or response.status_code == 429:
        raise SlackTokenRefusedError("slack_unavailable")
    try:
        body = response.json()
    except ValueError:
        raise SlackTokenRefusedError("slack_unreadable") from None
    if not isinstance(body, dict) or body.get("ok") is not True:
        error = body.get("error") if isinstance(body, dict) else None
        if error in ("invalid_auth", "account_inactive", "token_revoked", "token_expired"):
            raise SlackTokenRefusedError("token_invalid")
        raise SlackTokenRefusedError("slack_refused")
    granted = frozenset(
        s.strip() for s in response.headers.get("x-oauth-scopes", "").split(",") if s.strip()
    )
    if not granted >= REQUIRED_SCOPES:
        raise SlackTokenRefusedError("missing_scopes")
    if not granted <= REQUIRED_SCOPES:
        raise SlackTokenRefusedError("scopes_too_broad")
    return SlackIdentity(
        team_id=str(body["team_id"]),
        team_name=str(body.get("team", "")),
        user_id=str(body["user_id"]),
        scopes=granted,
    )


def revoke(client: httpx.Client, token: str) -> None:
    """``auth.revoke``: best effort when the owner disconnects. An already dead token is fine."""
    try:
        client.post(f"{API}/auth.revoke", headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError:
        return


def check_response(body: object) -> None:
    """Web API answer check for the connector: token problems become ``TokenRevokedError``."""
    if isinstance(body, dict) and body.get("ok") is False:
        error = body.get("error")
        if error in (
            "invalid_auth",
            "account_inactive",
            "token_revoked",
            "token_expired",
            "not_authed",
        ):
            raise TokenRevokedError(str(error))
        raise ProviderError(200, error if isinstance(error, str) else None)

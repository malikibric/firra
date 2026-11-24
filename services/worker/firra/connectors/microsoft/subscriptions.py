"""Graph change-notification subscriptions for one connection (Outlook messages or events).

Docs checked: POST /subscriptions (changeType, notificationUrl + lifecycleNotificationUrl, both
validated by Graph with a validationToken round-trip to apps/api; resource without the base URL;
clientState up to 128 characters; query strings on notificationUrl are delivered back), PATCH to
renew, 409 for a duplicate (same resource and changeType), maximum lifetime 10,080 minutes for
Outlook messages and events. Notifications only wake a ``sync.incremental``; the data itself is
always read through the normal sync path.

The notification URL carries ``w`` (workspace) and ``c`` (connection) so apps/api can route it
without a cross-tenant lookup; apps/api then compares the clientState with the sha256 stored here.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from firra.connectors.microsoft.graph import GRAPH, ConflictError, GraphClient, NotFoundError

LIFETIME = timedelta(days=3)
RENEW_AFTER = timedelta(hours=6)


@dataclass(frozen=True)
class PushTarget:
    notification_url: str
    workspace_id: UUID
    connection_id: UUID
    # "messages" or "events", relative to the connection's user path.
    collection: str


def _state(subscription: dict[str, Any], client_state_sha256: str, now: datetime) -> dict[str, Any]:
    return {
        "subscription_id": str(subscription["id"]),
        "client_state_sha256": client_state_sha256,
        "expires_at": str(subscription.get("expirationDateTime") or ""),
        "renewed_at": now.isoformat(),
    }


def _due(push: dict[str, Any] | None, now: datetime) -> bool:
    if not push or not push.get("subscription_id") or not push.get("renewed_at"):
        return True
    return now - datetime.fromisoformat(str(push["renewed_at"])) >= RENEW_AFTER


def ensure_subscription(
    graph: GraphClient,
    target: PushTarget,
    push: dict[str, Any] | None,
    *,
    now: datetime,
    force: bool = False,
) -> dict[str, Any] | None:
    """Creates or renews the subscription; returns the new push state, or None if none was due."""
    if not force and not _due(push, now):
        return None
    expires = (now + LIFETIME).isoformat().replace("+00:00", "Z")
    if push and push.get("subscription_id"):
        try:
            renewed = graph.request(
                "PATCH",
                f"{GRAPH}/subscriptions/{push['subscription_id']}",
                json={"expirationDateTime": expires},
            )
            return _state(renewed, str(push["client_state_sha256"]), now)
        except NotFoundError:
            pass  # expired or removed: create a new one

    resource = f"{graph.user_path.lstrip('/')}/{target.collection}"
    query = urlencode({"w": str(target.workspace_id), "c": str(target.connection_id)})
    url = f"{target.notification_url}?{query}"
    client_state = secrets.token_urlsafe(48)  # 64 characters
    body = {
        "changeType": "created,updated,deleted",
        "notificationUrl": url,
        "lifecycleNotificationUrl": url,
        "resource": resource,
        "expirationDateTime": expires,
        "clientState": client_state,
    }
    try:
        created = graph.request("POST", f"{GRAPH}/subscriptions", json=body)
    except ConflictError:
        # A subscription we lost track of exists for this resource: replace it.
        existing = graph.get(f"{GRAPH}/subscriptions").get("value", [])
        for item in existing:
            if str(item.get("resource", "")).lstrip("/").lower() == resource.lower():
                graph.request("DELETE", f"{GRAPH}/subscriptions/{item['id']}")
        created = graph.request("POST", f"{GRAPH}/subscriptions", json=body)
    return _state(created, hashlib.sha256(client_state.encode()).hexdigest(), now)

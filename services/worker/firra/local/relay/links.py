"""Connector links: a 256-bit secret per link, only its sha256 stored (relay.md section 8)."""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from firra.local.settings import ConnectorLink, load, save

SCOPES = ("intelligence", "sources")


def device_id(public_key: bytes) -> str:
    return hashlib.sha256(public_key).hexdigest()[:16]


def create(home: Path, name: str, scopes: list[str], *, device: str) -> tuple[ConnectorLink, str]:
    """Returns the stored link and the URL path token (shown to the owner once)."""
    secret = secrets.token_urlsafe(32)
    link = ConnectorLink(
        id=str(uuid4()),
        name=name,
        scopes=scopes,
        hash=hashlib.sha256(secret.encode()).hexdigest(),
        created_at=datetime.now(UTC).isoformat(),
    )
    settings = load(home)
    save(settings.model_copy(update={"relay_links": [*settings.relay_links, link]}), home)
    return link, f"{device}.{secret}"


def revoke(home: Path, link_id: str) -> bool:
    settings = load(home)
    kept = [link for link in settings.relay_links if link.id != link_id]
    if len(kept) == len(settings.relay_links):
        return False
    save(settings.model_copy(update={"relay_links": kept}), home)
    return True


def find(home: Path, secret: str) -> ConnectorLink | None:
    digest = hashlib.sha256(secret.encode()).hexdigest()
    for link in load(home).relay_links:
        if hmac.compare_digest(digest, link.hash):
            return link
    return None

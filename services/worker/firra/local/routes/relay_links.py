"""``/local/relay``: the relay address and connector links for claude.ai and ChatGPT.

The URL of a new link holds its secret and is returned once; only its hash is stored.
"""

import re
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter

from firra.local.readctx import ReadContext
from firra.local.relay import links
from firra.local.server import ApiError
from firra.local.settings import load, save
from firra_contracts.generated.local.relay_link_created import RelayLinkCreated
from firra_contracts.generated.local.relay_settings import RelaySettings

URL = re.compile(r"^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$")


def build_router(
    ctx: ReadContext,
    *,
    device_id: Callable[[], str | None],
    state: Callable[[], str],
    changed: Callable[[], None],
) -> APIRouter:
    router = APIRouter(prefix="/local/relay")
    home = ctx.home
    if home is None:
        raise ValueError("relay routes need the engine home")

    def view() -> dict[str, Any]:
        settings = load(home)
        return RelaySettings.model_validate(
            {
                "relay_url": settings.relay_url,
                "state": state(),
                "links": [
                    {k: v for k, v in link.model_dump().items() if k != "hash"}
                    for link in settings.relay_links
                ],
            }
        ).model_dump(mode="json")

    @router.get("")
    def get_settings() -> Any:
        return view()

    @router.put("")
    def put_settings(body: dict[str, Any]) -> Any:
        url = body.get("relay_url")
        if set(body) != {"relay_url"} or not (
            url is None or (isinstance(url, str) and URL.match(url))
        ):
            raise ApiError(422, "invalid_request", "relay_url must be an https address or null.")
        save(load(home).model_copy(update={"relay_url": url}), home)
        changed()
        return view()

    @router.post("/links")
    def create_link(body: dict[str, Any]) -> Any:
        name, scopes = body.get("name"), body.get("scopes", ["intelligence"])
        valid = (
            set(body) <= {"name", "scopes"}
            and isinstance(name, str)
            and 1 <= len(name) <= 60
            and isinstance(scopes, list)
            and 1 <= len(scopes) <= 2
            and len(set(scopes)) == len(scopes)
            and all(s in links.SCOPES for s in scopes)
        )
        settings = load(home)
        if not valid:
            raise ApiError(422, "invalid_request", "name and scopes are invalid.")
        if not settings.relay_url or (device := device_id()) is None:
            raise ApiError(409, "relay_not_configured", "Set the relay address first.")
        link, token = links.create(home, str(name), scopes, device=device)
        changed()
        return RelayLinkCreated.model_validate(
            {
                "id": link.id,
                "name": link.name,
                "scopes": link.scopes,
                "url": f"{settings.relay_url}/m/{token}",
            }
        ).model_dump(mode="json")

    @router.delete("/links/{link_id}", status_code=204)
    def delete_link(link_id: str) -> None:
        if not links.revoke(home, link_id):
            raise ApiError(404, "not_found", "No such link.")
        changed()

    return router

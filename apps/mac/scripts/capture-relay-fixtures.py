"""Capture real relay routes with a temporary home and https://relay.example.

Run from the repository root: uv run python apps/mac/scripts/capture-relay-fixtures.py
No running engine or relay is accessed. Volatile IDs, timestamps and the scratch link secret are
normalized so recapture is deterministic and the committed URL is visibly synthetic.
"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from firra.local.readctx import ReadContext
from firra.local.routes.relay_links import build_router
from firra.local.settings import load

out = Path(__file__).resolve().parent.parent / "Tests/FirraKitTests/Fixtures/LocalRoutes"
link_id = "00000000-0000-0000-0000-000000000002"


def capture(name: str, body: dict[str, Any]) -> None:
    if "id" in body:
        body["id"] = link_id
    if "url" in body:
        body["url"] = "https://relay.example/m/0000000000000001.synthetic-fixture-secret"
    for link in body.get("links", []):
        link["id"] = link_id
        link["created_at"] = "2026-10-05T12:00:00+00:00"
    (out / f"{name}.json").write_text(json.dumps(body, indent=2) + "\n")


with TemporaryDirectory(prefix="firra-relay-fixture-") as directory:
    home = Path(directory)
    app = FastAPI()
    app.include_router(
        build_router(
            cast(ReadContext, SimpleNamespace(home=home)),
            device_id=lambda: "0000000000000001",
            state=lambda: "online" if load(home).relay_url else "not_registered",
            changed=lambda: None,
        )
    )
    with TestClient(app) as client:
        response = client.get("/local/relay")
        response.raise_for_status()
        capture("relay-unconfigured", response.json())
        response = client.put("/local/relay", json={"relay_url": "https://relay.example"})
        response.raise_for_status()
        capture("relay-configured", response.json())
        response = client.post(
            "/local/relay/links", json={"name": "claude.ai", "scopes": ["intelligence"]}
        )
        response.raise_for_status()
        actual_id = response.json()["id"]
        capture("relay-link-created", response.json())
        response = client.get("/local/relay")
        response.raise_for_status()
        capture("relay-links", response.json())
        response = client.delete(f"/local/relay/links/{actual_id}")
        response.raise_for_status()
        if response.status_code != 204:
            raise RuntimeError("Unexpected revoke response")
        response = client.get("/local/relay")
        response.raise_for_status()
        if response.json()["links"] != []:
            raise RuntimeError("Revoked link remains in list")

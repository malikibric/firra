"""Owner settings in an atomic 0600 file; source access for the user's AI is always opt-in."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from firra_contracts import AiSettings


class ConnectorLink(BaseModel):
    """A connector link for claude.ai / ChatGPT. Only the sha256 of its secret is kept."""

    model_config = ConfigDict(extra="forbid", strict=True)
    id: str
    name: str = Field(min_length=1, max_length=60)
    scopes: list[str] = Field(min_length=1, max_length=2)
    hash: str = Field(pattern="^[0-9a-f]{64}$")
    created_at: str


class OwnerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    # The MCP source layer (excerpts of client mail, Slack and meetings) is off until the owner
    # switches it on (docs/plans/local/source-layer.md, decision T29).
    mcp_source_access: bool = False
    # get_pending_mail / submit_extractions / process_new_mail are not offered to the owner's AI
    # unless this is on: background extraction (firra/ai) does the work and each packet costs the
    # client AI about 15k tokens. Read when the MCP catalog is built (engine or `firra-mcp` start).
    mcp_processing_tools: bool = False
    # Months of mail, Slack and meeting text to keep (None = until the owner deletes it).
    body_retention_months: int | None = Field(default=12, ge=1, le=120)
    # MCP over a link (relay.md section 8): the relay's https address and the connector links.
    relay_url: str | None = Field(default=None, max_length=200)
    relay_links: list[ConnectorLink] = Field(default_factory=list, max_length=50)
    ai: AiSettings = Field(
        default_factory=lambda: AiSettings.model_validate(
            {"executor": "conversation_only", "backfill_months": 3}
        )
    )
    ai_revision: int = Field(default=0, ge=0)

    @model_validator(mode="before")
    @classmethod
    def _legacy_key(cls, data: Any) -> Any:
        """Older files used ``mcp_evidence_context`` for the same switch."""
        if isinstance(data, dict) and "mcp_evidence_context" in data:
            data = dict(data)
            legacy = data.pop("mcp_evidence_context")
            data.setdefault("mcp_source_access", legacy)
        return data


def engine_home() -> Path:
    return Path(
        os.environ.get("FIRRA_HOME", str(Path.home() / "Library/Application Support/Firra"))
    )


def load(home: Path | None = None) -> OwnerSettings:
    path = (home or engine_home()) / "settings.json"
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return OwnerSettings()
    try:
        return OwnerSettings.model_validate_json(raw)
    except ValidationError:
        raise ValueError("invalid owner settings") from None


def save(settings: OwnerSettings, home: Path | None = None) -> None:
    root = home or engine_home()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".settings-", dir=root)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(settings.model_dump(mode="json"), stream)
            stream.flush()
            os.fsync(stream.fileno())
        Path(name).replace(root / "settings.json")
    finally:
        Path(name).unlink(missing_ok=True)

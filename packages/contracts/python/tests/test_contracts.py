import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from firra_contracts import (
    AiExtractionOutput,
    AiSettings,
    CalendarEventRecord,
    ChatMessage,
    DiagnosticsPingPayload,
    EngineStatus,
    HandshakeMessage,
    IngestUploadPayload,
    JobStatus,
    Message,
    TaskEnvelope,
)

# Shared with test/contracts.test.ts so both generators must agree.
CASES: dict[str, dict[str, list[Any]]] = json.loads(
    (Path(__file__).parents[2] / "fixtures" / "cases.json").read_text()
)
ADAPTERS: dict[str, TypeAdapter[Any]] = {
    "TaskEnvelope": TypeAdapter(TaskEnvelope),
    "DiagnosticsPingPayload": TypeAdapter(DiagnosticsPingPayload),
    "JobStatus": TypeAdapter(JobStatus),
    "Message": TypeAdapter(Message),
    "CalendarEventRecord": TypeAdapter(CalendarEventRecord),
    "IngestUploadPayload": TypeAdapter(IngestUploadPayload),
    "HandshakeMessage": TypeAdapter(HandshakeMessage),
    "AiExtractionOutput": TypeAdapter(AiExtractionOutput),
    "AiSettings": TypeAdapter(AiSettings),
    "EngineStatus": TypeAdapter(EngineStatus),
    "ChatMessage": TypeAdapter(ChatMessage),
}


def _cases(kind: str) -> list[tuple[str, Any]]:
    return [(name, value) for name in ADAPTERS for value in CASES[name][kind]]


@pytest.mark.parametrize(("name", "value"), _cases("valid"))
def test_accepts_valid(name: str, value: Any) -> None:
    ADAPTERS[name].validate_python(value, strict=False)


@pytest.mark.parametrize(("name", "value"), _cases("invalid"))
def test_rejects_invalid(name: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        ADAPTERS[name].validate_python(value)


def test_models_forbid_extra_fields() -> None:
    for model in (TaskEnvelope, DiagnosticsPingPayload, Message, CalendarEventRecord):
        assert issubclass(model, BaseModel)
        assert model.model_config.get("extra") == "forbid"

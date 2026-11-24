from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from firra.db.jobs import ClaimedJob
from firra.db.types import JobId, WorkspaceId
from firra.http import ProviderError
from firra.llm.service import Llm
from firra.tasks.context import TaskContext
from firra.tasks.errors import PermanentTaskError
from firra.tasks.handlers.diagnostics import handle_llm_probe
from firra_contracts import DiagnosticsLlmProbePayload
from tests.unit.test_llm_service import MODELS, NOW, FakeBackend, FakeUsage

JOB = ClaimedJob(
    workspace_id=WorkspaceId(uuid4()),
    id=JobId(uuid4()),
    type="diagnostics.llm_probe",
    attempts=1,
    max_attempts=3,
)
PAYLOAD = DiagnosticsLlmProbePayload.model_validate({"requested_at": "2026-09-28T20:00:00Z"})


def _ctx(llm: Llm | None) -> TaskContext:
    return cast(TaskContext, SimpleNamespace(llm=llm))


def test_probe_reports_every_model() -> None:
    backend = FakeBackend(['{"ok": true, "color": "blue"}', '{"ok": true, "color": "Blue"}'])
    result = handle_llm_probe(
        _ctx(Llm(backend, MODELS, FakeUsage(), clock=lambda: NOW)), JOB, PAYLOAD
    )

    assert result["location"] == "eu"
    assert {k: v["ok"] for k, v in result["models"].items()} == {
        "extract": True,
        "synth": False,  # "Blue" is not lower case as asked
        "embed": True,
    }
    assert result["all_ok"] is False
    assert backend.calls[0]["thinking_budget"] == 0


def test_provider_errors_are_reported_per_model() -> None:
    class Missing(FakeBackend):
        def generate(self, model: str, **kwargs: Any) -> Any:
            raise ProviderError(404, "NOT_FOUND")

    result = handle_llm_probe(
        _ctx(Llm(Missing([]), MODELS, FakeUsage(), clock=lambda: NOW)), JOB, PAYLOAD
    )
    assert result["models"]["synth"] | {"duration_ms": 0} == {
        "model": "gemini-3.8-flash",
        "ok": False,
        "error_class": "provider",
        "provider_status": 404,
        "provider_reason": "NOT_FOUND",
        "duration_ms": 0,
    }


def test_probe_without_vertex_fails_permanently() -> None:
    with pytest.raises(PermanentTaskError, match="llm_not_configured"):
        handle_llm_probe(_ctx(None), JOB, PAYLOAD)

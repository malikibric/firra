import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from firra.db.jobs import ClaimedJob
from firra.db.types import WorkspaceId
from firra.http import ProviderError
from firra.llm.models import Purpose
from firra.llm.service import Llm
from firra.llm.untrusted import wrap_untrusted
from firra.tasks.context import TaskContext
from firra.tasks.errors import PermanentTaskError
from firra_contracts import DiagnosticsLlmProbePayload, DiagnosticsPingPayload


def handle_ping(
    _ctx: TaskContext, _job: ClaimedJob, payload: DiagnosticsPingPayload
) -> dict[str, Any]:
    """Proves the api -> Cloud Tasks -> worker -> DB round-trip; records timings only."""
    return {
        "requested_at": payload.requested_at.isoformat(),
        "pong_at": datetime.now(UTC).isoformat(),
    }


class _ProbeAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    color: str = Field(max_length=20)


_PROBE_SYSTEM = "You are a connectivity check. Answer only with JSON that matches the schema."
_PROBE_USER = (
    wrap_untrusted("probe", "The sky on a clear day is blue.")
    + "\nSet ok to true and color to the color named in the record, in lower case."
)


def _measure(model: str, call: Callable[[], bool]) -> dict[str, Any]:
    started = time.monotonic()
    result: dict[str, Any] = {"model": model}
    try:
        result["ok"] = call()
    except ProviderError as exc:
        result |= {"ok": False, "error_class": "provider", "provider_status": exc.status}
        if exc.reason:
            result["provider_reason"] = exc.reason
    except Exception as exc:  # the probe reports every failure per model
        result |= {"ok": False, "error_class": type(exc).__name__}
    result["duration_ms"] = round((time.monotonic() - started) * 1000)
    return result


def _probe_structured(
    llm: Llm, workspace_id: WorkspaceId, job: ClaimedJob, purpose: Purpose
) -> dict[str, Any]:
    def call() -> bool:
        answer = llm.structured(
            workspace_id=workspace_id,
            job_id=job.id,
            purpose=purpose,
            prompt_version="probe.v1",
            system=_PROBE_SYSTEM,
            user=_PROBE_USER,
            output=_ProbeAnswer,
            thinking_budget=0 if purpose == "extract" else None,
        )
        return answer.value.ok and answer.value.color.strip() == "blue"

    return _measure(llm.models.model_for(purpose), call)


def handle_llm_probe(
    ctx: TaskContext, job: ClaimedJob, _payload: DiagnosticsLlmProbePayload
) -> dict[str, Any]:
    """Calls each configured model once on the EU endpoint (fictional text, a fraction of a cent)
    so a model that is not served there fails here, not in the pipeline (the design notes)."""
    llm = ctx.llm
    if llm is None:
        raise PermanentTaskError("llm_not_configured")
    workspace_id = job.workspace_id
    models = {
        purpose: _probe_structured(llm, workspace_id, job, purpose)
        for purpose in ("extract", "synth")
    }
    models["embed"] = _measure(
        llm.models.model_for("embed"),
        lambda: len(llm.embed(workspace_id=workspace_id, job_id=job.id, text="blue sky")) == 768,
    )
    return {
        "location": llm.models.location,
        "models": models,
        "all_ok": all(result["ok"] for result in models.values()),
        "probed_at": datetime.now(UTC).isoformat(),
    }

"""Fake CLI/clock: acknowledgement, action suppression, selection and limit progress."""

from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from tests.unit.ai.test_processor import WS, Repo, record

from firra.ai.executor import ExecutorKind, ExecutorLimitReached, FakeExecutor, executor_for
from firra.ai.local import AiRuntime
from firra.ai.packet import build_packet
from firra.ai.processor import PacketProcessor
from firra.cards.schema import Fact
from firra.cards.word import deterministic_card
from firra.db.ai_processing import AiProcessingRepo
from firra.db.jobs import ClaimedJob
from firra.db.types import JobId, WorkspaceId
from firra.llm.prompts import load_prompt
from firra.local.settings import OwnerSettings, load, save
from firra.tasks.errors import ContinueTask
from firra.tasks.handlers.process import handle_process_records
from firra_contracts import AiSettings, ProcessRecordsPayload
from firra_contracts.generated.local.engine_status import Tool


class ProbedFake(FakeExecutor):
    def __init__(self, answers: list[Any] | None = None) -> None:
        super().__init__(answers or [], kind=ExecutorKind.claude_code)
        self.signed_in = True

    def detect(self) -> Tool:
        return Tool.model_validate(
            {
                "tool": "claude_code",
                "installed": True,
                "signed_in": self.signed_in,
                "version": "fixture",
            }
        )


class ScheduleRepo:
    def __init__(self) -> None:
        self.job: dict[str, Any] | None = None
        self.created = 0

    def snapshot(self, **kwargs: Any) -> dict[str, Any]:
        return {"job": self.job, "queue": {"pending": 37, "done": 2, "rejected_items": 1}}

    def request(self, **kwargs: Any) -> tuple[Any, str] | None:
        if self.job and self.job["status"] in ("queued", "running"):
            return None
        self.created += 1
        job_id = uuid4()
        self.job = {
            "id": job_id,
            "status": "queued",
            "error_class": None,
            "progress": {"ai_settings_revision": kwargs["revision"]},
        }
        return job_id, str(job_id)


def settings(**patch: Any) -> AiSettings:
    return AiSettings.model_validate(
        {
            "executor": "claude_code",
            "backfill_months": 3,
            "background_acknowledged": True,
            **patch,
        }
    )


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    repo, fake, pool = ScheduleRepo(), ProbedFake(), MagicMock()
    monkeypatch.setattr("firra.ai.local.LocalAiRepo", lambda p: repo)
    models: list[str | None] = []

    def factory(model: str | None) -> ProbedFake:
        models.append(model)
        return fake

    now = [datetime(2026, 10, 5, 12, tzinfo=UTC)]
    runtime = AiRuntime(pool, WorkspaceId(uuid4()), tmp_path, claude=factory, clock=lambda: now[0])
    return runtime, repo, fake, models, now


def test_settings_are_private_atomic_preserve_owner_flags_and_choose_at_job_start(
    runtime: Any,
) -> None:
    ai, _, _, models, _ = runtime
    save(OwnerSettings(mcp_source_access=True), ai.home)
    assert executor_for(ai.pool) is None
    with pytest.raises(ValueError, match="background_acknowledgement_required"):
        ai.update(settings(background_acknowledged=False))
    with pytest.raises(ValueError, match="codex_not_available"):
        ai.update(settings(executor="codex"))
    ai.update(settings(model="fixture-a"))
    executor_for(ai.pool)
    ai.update(settings(model="fixture-b"))
    executor_for(ai.pool)
    assert models == ["fixture-a", "fixture-b"]
    assert load(ai.home).mcp_source_access is True
    assert (ai.home / "settings.json").stat().st_mode & 0o777 == 0o600
    assert not list(ai.home.glob(".settings-*"))


@pytest.mark.parametrize(
    "error,state",
    [
        ("executor_not_signed_in", "needs_signin"),
        ("executor_not_installed", "tool_missing"),
    ],
)
def test_failed_action_is_not_requested_until_settings_change_or_successful_probe(
    runtime: Any,
    error: str,
    state: str,
) -> None:
    ai, repo, fake, _, _ = runtime
    ctx = MagicMock()
    ai.update(settings())
    assert ai.request(ctx, "sweep")
    assert ai.request(ctx, "ingest") is False
    repo.job.update(status="failed", error_class=error)
    assert ai.status().state.value == state
    assert ai.status().queue.pending == 37
    for _ in range(3):
        assert ai.request(ctx, "sweep") is False
    fake.signed_in = False
    ai.probe()
    assert ai.request(ctx, "sweep") is False
    fake.signed_in = True
    ai.probe()
    assert ai.status().state.value == "idle"
    assert ai.request(ctx, "sweep")
    repo.job.update(status="failed", error_class=error)
    ai.update(settings(model="changed"))
    assert ai.request(ctx, "sweep")
    assert repo.created == 3
    ai.update(settings(executor="conversation_only"))
    assert ai.request(ctx, "sweep") is False


def test_limit_uses_fake_clock_and_persists_the_reset_in_progress(runtime: Any) -> None:
    ai, repo, _, _, now = runtime
    reset = now[0] + timedelta(hours=2)
    fake = FakeExecutor([ExecutorLimitReached(reset)])
    processor = PacketProcessor(
        fake, cast(AiProcessingRepo, Repo()), load_prompt("extract.v6"), WS, clock=lambda: now[0]
    )
    with pytest.raises(ContinueTask) as result:
        processor.process([record()])
    assert result.value.delay_seconds == 7200
    assert result.value.progress["resume_at"] == reset.isoformat()
    assert len(fake.calls) == 1
    ai.update(settings())
    ai.request(MagicMock(), "sweep")
    repo.job["progress"].update(result.value.progress)
    status = ai.status()
    assert status.state.value == "waiting_for_limit" and status.resume_at == reset
    now[0] = reset
    assert ai.status().state.value == "working"


def test_deterministic_cards_keep_citations_and_the_existing_number_date_checks() -> None:
    facts = [Fact(id="f1", text="A commitment is due on 2026-10-05.", basis="metadata")]
    worded = deterministic_card(play="fix_first", facts=facts)
    assert worded.card is not None and worded.card.fact_ids == ["f1"]
    assert worded.card.interpretation == facts[0].text and worded.attempts == 0
    assert not worded.violations
    unsafe = Fact(id="f1", text="The client said twenty accounts.", basis="metadata")
    rejected = deterministic_card(play="fix_first", facts=[unsafe])
    assert rejected.card is None
    assert Counter(v.split(":")[0] for v in rejected.violations) == {"number_word": 1}


def test_restart_before_reset_preserves_delay_without_invoking_the_executor(runtime: Any) -> None:
    ai, _, fake, _, now = runtime
    ai.update(settings())
    reset = now[0] + timedelta(minutes=30)
    progress = {
        "ai_settings_revision": load(ai.home).ai_revision,
        "resume_at": reset.isoformat(),
        "retry_at": reset.isoformat(),
    }
    job = ClaimedJob(ai.workspace_id, JobId(uuid4()), "process.records", 1, 5, progress)
    ctx = MagicMock(pool=ai.pool, processing_enabled=True)
    with pytest.raises(ContinueTask) as continuation:
        handle_process_records(ctx, job, ProcessRecordsPayload.model_validate({"trigger": "sweep"}))
    assert continuation.value.delay_seconds == 1800
    assert continuation.value.progress == progress
    assert fake.calls == []
    ctx.jobs.update_progress.assert_not_called()


def test_contract_batch_size_accepts_forty_bounded_records() -> None:
    packet = build_packet(
        [record() for _ in range(41)], {}, prompt_version="extract.v6", batch_records=40
    )
    assert len(packet.handles) == 40
    assert len(packet.text) <= 60_000

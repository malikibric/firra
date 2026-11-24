"""Meeting briefs: the deterministic brief is stored first and is free; rewording its questions
(prompt meeting.v1) is paid and runs only behind the processing switch, best effort."""

from datetime import UTC, datetime
from typing import Any

from firra.ai.executor import is_local
from firra.brief.meeting import load_config
from firra.brief.word import PROMPT_VERSION, word_questions
from firra.db.jobs import ClaimedJob
from firra.db.meetings import Meeting, MeetingRepo
from firra.db.types import WorkspaceId
from firra.logging import get_logger
from firra.tasks.context import TaskContext
from firra_contracts.generated.tasks.brief_meetings import BriefMeetingsPayload

log = get_logger(__name__)


def _reword(ctx: TaskContext, repo: MeetingRepo, job: ClaimedJob, meeting: Meeting) -> bool:
    """Best effort: any failure leaves the stored deterministic questions as they are."""
    if is_local(ctx.pool) or not ctx.processing_enabled or ctx.llm is None:
        return False
    workspace_id = WorkspaceId(job.workspace_id)
    stored = repo.stored_for_wording(workspace_id=workspace_id, meeting=meeting)
    if stored is None:
        return False
    try:
        worded = word_questions(
            ctx.llm,
            workspace_id=workspace_id,
            job_id=job.id,
            client_name=stored.client_name,
            questions=stored.questions,
        )
        if worded.texts is None:
            log.info("brief.reword_dropped", violations=len(worded.violations))
            return False
        questions = [
            {**question, "text": text}
            for question, text in zip(stored.questions, worded.texts, strict=True)
        ]
        return repo.reword(
            workspace_id=workspace_id,
            brief=stored,
            questions=questions,
            model=ctx.llm.models.model_for("synth"),
            prompt_version=PROMPT_VERSION,
        )
    except Exception as error:
        log.warning("brief.reword_failed", error_class=type(error).__name__)
        return False


def handle_brief_meetings(
    ctx: TaskContext, job: ClaimedJob, payload: BriefMeetingsPayload, *, now: datetime | None = None
) -> dict[str, Any]:
    at = now or datetime.now(UTC)
    cfg = load_config()
    repo = MeetingRepo(ctx.pool)
    meetings = repo.upcoming(workspace_id=job.workspace_id, now=at, cfg=cfg)
    written = 0
    reworded = 0
    for meeting in meetings:
        facts = repo.load_facts(workspace_id=job.workspace_id, meeting=meeting, now=at, cfg=cfg)
        if facts is None:
            continue
        saved = repo.save(
            workspace_id=job.workspace_id,
            meeting=meeting,
            facts=facts,
            now=at,
            cfg=cfg,
            deterministic=is_local(ctx.pool),
        )
        written += saved
        if saved and _reword(ctx, repo, job, meeting):
            reworded += 1
    log.info("brief.meetings", workspace_id=str(job.workspace_id), count=written, reworded=reworded)
    return {
        "meetings": len(meetings),
        "briefs_written": written,
        "briefs_reworded": reworded,
        "trigger": payload.trigger.value,
    }

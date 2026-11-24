"""signals.evaluate: rules, coverage guard and account assessment per client (P2 D10, P2b D6).

Deterministic and free of model calls, so it does not depend on FIRRA_PROCESSING_ENABLED. Clients
are handled one by one, each in its own transaction (signals, their history and the day's live
assessment together); a client whose evaluation fails does not stop the others. Only active
clients are assessed live; churned clients are assessed by the replay runner. Logs carry counts
and client ids only.
"""

import logging
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from firra.ai.executor import is_local
from firra.ai.local import runtime_for
from firra.assessment.assess import assess
from firra.assessment.complaints import unresolved_complaints
from firra.assessment.config import load_assessment_config, load_plays_config
from firra.assessment.guard import coverage_guard, volume_rule_types
from firra.db.assessments import AssessmentRepo
from firra.db.jobs import ClaimedJob
from firra.db.local_ai import LocalAiRepo
from firra.db.routines import RoutinesRepo
from firra.db.signals import SignalRepo
from firra.db.types import WorkspaceId
from firra.signals.config import load_config
from firra.signals.engine import evaluate, validate_config
from firra.tasks.context import TaskContext
from firra.tasks.errors import PermanentTaskError
from firra_contracts import SignalsEvaluatePayload, TaskEnvelope

log = logging.getLogger("firra.signals")


def _reference(as_of: date | None) -> datetime:
    if as_of is None:
        return datetime.now(UTC)
    return datetime.combine(as_of, time.max, tzinfo=UTC)


def handle_signals_evaluate(
    ctx: TaskContext, job: ClaimedJob, payload: SignalsEvaluatePayload
) -> dict[str, Any]:
    config = load_config()
    try:
        validate_config(config)
        assessment_cfg = load_assessment_config()
        plays_cfg = load_plays_config()
    except ValueError as exc:
        raise PermanentTaskError("signals_config_invalid") from exc
    volume = volume_rule_types()
    tolerance = timedelta(days=assessment_cfg.coverage.tolerance_days)
    repo = SignalRepo(ctx.pool)
    assessments = AssessmentRepo(ctx.pool)
    workspace_id = WorkspaceId(job.workspace_id)
    ref = _reference(payload.as_of)
    written: Counter[str] = Counter()
    invalid: Counter[str] = Counter()
    states: Counter[str] = Counter()
    caveat_codes: Counter[str] = Counter()
    resolved = failed = evaluated = events = 0
    for client in repo.clients(workspace_id=workspace_id):
        try:
            facts = repo.load_facts(workspace_id=workspace_id, client_id=client.id, ref=ref)
            if facts is None:
                continue
            result = evaluate(facts, config)
            drafts, guard_caveats = coverage_guard(
                facts, result.drafts, volume, tolerance=tolerance
            )
            assessment = None
            if client.status == "active":
                assessment = assess(
                    drafts,
                    facts,
                    assessments.previous_live(
                        workspace_id=workspace_id, client_id=client.id, as_of=facts.ref.date()
                    ),
                    assessment_cfg,
                    plays_cfg,
                    volume_types=volume,
                    unresolved_complaints=unresolved_complaints,
                    guard_caveats=guard_caveats,
                )
            saved = repo.save(
                workspace_id=workspace_id,
                client_id=client.id,
                drafts=drafts,
                rule_version=config.version,
                as_of=facts.ref,
                job_id=job.id,
                assessment=assessment,
                assessment_version=assessment_cfg.version,
            )
        except Exception:
            failed += 1
            log.exception("signals evaluation failed", extra={"client_id": str(client.id)})
            continue
        evaluated += 1
        resolved += saved.resolved
        events += saved.events
        written.update(d.type for d in drafts)
        if assessment is not None and saved.assessment_id is not None:
            states[assessment.risk_state] += 1
            caveat_codes.update(c.code for c in assessment.caveats)
        invalid.update(result.invalid)
    if failed and not evaluated:
        raise PermanentTaskError("signals_evaluation_failed")
    if is_local(ctx.pool):
        _request_local_followup(ctx, job, ref)
    return {
        "trigger": payload.trigger.value,
        "rule_version": config.version,
        "clients_evaluated": evaluated,
        "clients_failed": failed,
        "signals_by_type": dict(written),
        "signals_resolved": resolved,
        "signal_events": events,
        "assessment_version": assessment_cfg.version,
        "assessments_by_state": dict(states),
        "caveats_by_code": dict(caveat_codes),
        "drafts_invalid": dict(invalid),
        "completed_at": datetime.now(UTC).isoformat(),
    }


def _request_local_followup(ctx: TaskContext, job: ClaimedJob, ref: datetime) -> None:
    # Assessment is already persisted in signals.save; routines must read it afterwards.
    RoutinesRepo(ctx.pool).ensure_routines(workspace_id=job.workspace_id)
    runtime = runtime_for(ctx.pool)
    now = runtime.clock() if runtime is not None else ref
    scheduled: list[tuple[str, str, dict[str, Any]]] = [
        (
            "synth.cards",
            f"cards:{job.id}",
            {
                "trigger": "schedule",
                "week": (ref.date() - timedelta(days=ref.weekday())).isoformat(),
            },
        ),
        ("brief.meetings", f"brief:{job.id}", {"trigger": "sweep"}),
    ]
    for routine_id, day in LocalAiRepo(ctx.pool).routines(workspace_id=job.workspace_id, now=now):
        scheduled.append(
            (
                "routines.run",
                f"routine:{routine_id}:{day}",
                {
                    "routine_id": str(routine_id),
                    "scheduled_for": day.isoformat(),
                },
            )
        )
    for task_type, key, payload in scheduled:
        job_id = ctx.jobs.create(
            workspace_id=job.workspace_id, job_type=task_type, idempotency_key=key, payload=payload
        )
        if job_id is not None:
            ctx.queue.enqueue(
                TaskEnvelope(
                    workspace_id=job.workspace_id,
                    job_id=job_id,
                    idempotency_key=key,
                    type=task_type,
                    payload=payload,
                )
            )

"""Runs one stored replay run end to end; shared by the task handler and the local CLI."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from firra.assessment.config import load_assessment_config, load_plays_config
from firra.assessment.guard import volume_rule_types
from firra.assessment.replay import replay_series, replay_steps
from firra.assessment.summary import ClientSeries, RunSummary, Step, summarize
from firra.db.replay import ReplayRepo
from firra.db.signals import SignalRepo
from firra.db.types import WorkspaceId
from firra.logging import get_logger
from firra.signals.config import load_config
from firra.signals.engine import validate_config
from firra.signals.facts import ClientFacts

log = get_logger(__name__)


class ReplayNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class ReplayResult:
    summary: RunSummary
    assessments: int


def run_replay(
    signals_repo: SignalRepo,
    replay_repo: ReplayRepo,
    *,
    workspace_id: WorkspaceId,
    run_id: UUID,
    job_id: UUID | None = None,
) -> ReplayResult:
    """Deterministic for equal data: a rerun deletes the run's rows and writes them again."""
    signals_cfg = load_config()
    validate_config(signals_cfg)
    assessment_cfg = load_assessment_config()
    plays_cfg = load_plays_config()
    volume = volume_rule_types()
    run = replay_repo.start(
        workspace_id=workspace_id,
        run_id=run_id,
        assessment_version=assessment_cfg.version,
        rule_version=signals_cfg.version,
        job_id=job_id,
    )
    if run is None:
        raise ReplayNotFoundError(str(run_id))
    churn = replay_repo.churn_facts(workspace_id=workspace_id)
    series_by_client: list[ClientSeries] = []
    written = 0
    for client in signals_repo.clients(workspace_id=workspace_id):
        churned_at, notice_at, first_known_at = churn.get(client.id, (None, None, None))
        steps = replay_steps(
            run["window_start"],
            run["window_end"],
            run["step_days"],
            stop=churned_at if client.status == "churned" else None,
        )

        def load(ref: datetime, client_id: UUID = client.id) -> ClientFacts | None:
            return signals_repo.load_facts(workspace_id=workspace_id, client_id=client_id, ref=ref)

        series = replay_series(
            load,
            steps,
            signals=signals_cfg,
            assessment=assessment_cfg,
            plays=plays_cfg,
            volume_types=volume,
        )
        written += replay_repo.write_client(
            workspace_id=workspace_id,
            run_id=run_id,
            client_id=client.id,
            series=series,
            assessment_version=assessment_cfg.version,
            rule_version=signals_cfg.version,
        )
        series_by_client.append(
            ClientSeries(
                client_id=client.id,
                status=client.status,
                steps=[
                    Step(a.as_of, a.risk_state, tuple(c.code for c in a.caveats)) for a in series
                ],
                churned_at=churned_at,
                notice_at=notice_at,
                first_known_at=first_known_at,
            )
        )
    summary = summarize(
        series_by_client,
        window_start=run["window_start"],
        window_end=run["window_end"],
        step_days=run["step_days"],
    )
    replay_repo.finish(workspace_id=workspace_id, run_id=run_id, summary=summary.as_json())
    log.info(
        "replay.finished",
        run_id=str(run_id),
        clients=len(series_by_client),
        assessments=written,
        losses=summary.totals["losses"],
    )
    return ReplayResult(summary=summary, assessments=written)

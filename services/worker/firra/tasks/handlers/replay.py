"""assessment.replay: weekly assessments without hindsight for one requested replay run (D8).

Free of model calls, so it does not depend on FIRRA_PROCESSING_ENABLED. A failure marks the run
`failed` with an error class and is retried by the task queue; a retry starts the run over.
"""

from typing import Any

from firra.assessment.runner import ReplayNotFoundError, run_replay
from firra.db.jobs import ClaimedJob
from firra.db.replay import ReplayRepo
from firra.db.signals import SignalRepo
from firra.db.types import WorkspaceId
from firra.tasks.context import TaskContext
from firra.tasks.errors import PermanentTaskError
from firra_contracts.generated.tasks.assessment_replay import AssessmentReplayPayload


def handle_assessment_replay(
    ctx: TaskContext, job: ClaimedJob, payload: AssessmentReplayPayload
) -> dict[str, Any]:
    workspace_id = WorkspaceId(job.workspace_id)
    replay = ReplayRepo(ctx.pool)
    try:
        result = run_replay(
            SignalRepo(ctx.pool),
            replay,
            workspace_id=workspace_id,
            run_id=payload.replay_run_id,
            job_id=job.id,
        )
    except ReplayNotFoundError as exc:
        raise PermanentTaskError("replay_run_not_found") from exc
    except Exception:
        replay.fail(
            workspace_id=workspace_id, run_id=payload.replay_run_id, error_class="replay_failed"
        )
        raise
    return {
        "replay_run_id": str(payload.replay_run_id),
        "assessments": result.assessments,
        **result.summary.totals,
    }

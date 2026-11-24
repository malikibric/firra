"""Task type -> (payload model, handler). Keep in sync with packages/contracts/src/tasks.ts."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from firra.db.jobs import ClaimedJob
from firra.tasks.context import TaskContext
from firra.tasks.handlers.brief import handle_brief_meetings
from firra.tasks.handlers.cards import handle_synth_cards
from firra.tasks.handlers.diagnostics import handle_llm_probe, handle_ping
from firra.tasks.handlers.ingest_upload import handle_ingest_upload
from firra.tasks.handlers.memory import handle_memory_rebuild
from firra.tasks.handlers.needs import handle_needs_update
from firra.tasks.handlers.onboarding import handle_onboarding_suggest
from firra.tasks.handlers.process import handle_process_records
from firra.tasks.handlers.replay import handle_assessment_replay
from firra.tasks.handlers.routines import handle_routines_run
from firra.tasks.handlers.signals import handle_signals_evaluate
from firra.tasks.handlers.sync import handle_backfill_chunk, handle_incremental
from firra.tasks.handlers.sync_slack import handle_sync_slack
from firra.tasks.handlers.vault import handle_vault_rehydrate
from firra_contracts import (
    DiagnosticsLlmProbePayload,
    DiagnosticsPingPayload,
    IngestUploadPayload,
    MemoryRebuildPayload,
    ProcessRecordsPayload,
    SignalsEvaluatePayload,
    SyncBackfillChunkPayload,
    SyncIncrementalPayload,
    SyncSlackPayload,
)
from firra_contracts.generated.tasks.assessment_replay import AssessmentReplayPayload
from firra_contracts.generated.tasks.brief_meetings import BriefMeetingsPayload
from firra_contracts.generated.tasks.needs_update import NeedsUpdatePayload
from firra_contracts.generated.tasks.onboarding_suggest import OnboardingSuggestPayload
from firra_contracts.generated.tasks.routines_run import RoutinesRunPayload
from firra_contracts.generated.tasks.synth_cards import SynthCardsPayload
from firra_contracts.generated.tasks.vault_rehydrate import VaultRehydratePayload


@dataclass(frozen=True, slots=True)
class TaskSpec[P: BaseModel]:
    payload_model: type[P]
    # Returns a progress patch merged into job.progress on success.
    handler: Callable[[TaskContext, ClaimedJob, P], dict[str, Any]]


REGISTRY: dict[str, TaskSpec[Any]] = {
    "vault.rehydrate": TaskSpec(VaultRehydratePayload, handle_vault_rehydrate),
    "diagnostics.ping": TaskSpec(DiagnosticsPingPayload, handle_ping),
    "diagnostics.llm_probe": TaskSpec(DiagnosticsLlmProbePayload, handle_llm_probe),
    "ingest.upload": TaskSpec(IngestUploadPayload, handle_ingest_upload),
    "sync.backfill_chunk": TaskSpec(SyncBackfillChunkPayload, handle_backfill_chunk),
    "sync.incremental": TaskSpec(SyncIncrementalPayload, handle_incremental),
    "sync.slack": TaskSpec(SyncSlackPayload, handle_sync_slack),
    "onboarding.suggest": TaskSpec(OnboardingSuggestPayload, handle_onboarding_suggest),
    "process.records": TaskSpec(ProcessRecordsPayload, handle_process_records),
    "signals.evaluate": TaskSpec(SignalsEvaluatePayload, handle_signals_evaluate),
    "memory.rebuild": TaskSpec(MemoryRebuildPayload, handle_memory_rebuild),
    "needs.update": TaskSpec(NeedsUpdatePayload, handle_needs_update),
    "brief.meetings": TaskSpec(BriefMeetingsPayload, handle_brief_meetings),
    "routines.run": TaskSpec(RoutinesRunPayload, handle_routines_run),
    "synth.cards": TaskSpec(SynthCardsPayload, handle_synth_cards),
    "assessment.replay": TaskSpec(AssessmentReplayPayload, handle_assessment_replay),
}

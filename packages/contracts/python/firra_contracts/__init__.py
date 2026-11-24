"""Contracts shared with the TypeScript side.

Generated from packages/contracts/schemas (the source of truth).
"""

from firra_contracts.generated.enums import ActorType, AppLocale, JobStatus, MemberRole
from firra_contracts.generated.local.ai_extraction_output import AiExtractionOutput
from firra_contracts.generated.local.ai_settings import AiSettings
from firra_contracts.generated.local.engine_status import EngineStatus
from firra_contracts.generated.local.handshake import HandshakeMessage
from firra_contracts.generated.records.calendar_event import CalendarEventRecord
from firra_contracts.generated.records.chat_message import ChatMessage
from firra_contracts.generated.records.message import Message, Participant
from firra_contracts.generated.task_envelope import TaskEnvelope
from firra_contracts.generated.tasks.diagnostics_llm_probe import DiagnosticsLlmProbePayload
from firra_contracts.generated.tasks.diagnostics_ping import DiagnosticsPingPayload
from firra_contracts.generated.tasks.ingest_upload import IngestUploadPayload
from firra_contracts.generated.tasks.memory_rebuild import MemoryRebuildPayload
from firra_contracts.generated.tasks.onboarding_suggest import OnboardingSuggestPayload
from firra_contracts.generated.tasks.process_records import ProcessRecordsPayload
from firra_contracts.generated.tasks.signals_evaluate import SignalsEvaluatePayload
from firra_contracts.generated.tasks.sync_backfill_chunk import SyncBackfillChunkPayload
from firra_contracts.generated.tasks.sync_incremental import SyncIncrementalPayload
from firra_contracts.generated.tasks.sync_slack import SyncSlackPayload

__all__ = [
    "ActorType",
    "AiExtractionOutput",
    "AiSettings",
    "AppLocale",
    "CalendarEventRecord",
    "ChatMessage",
    "DiagnosticsLlmProbePayload",
    "DiagnosticsPingPayload",
    "EngineStatus",
    "HandshakeMessage",
    "IngestUploadPayload",
    "JobStatus",
    "MemberRole",
    "MemoryRebuildPayload",
    "Message",
    "OnboardingSuggestPayload",
    "Participant",
    "ProcessRecordsPayload",
    "SignalsEvaluatePayload",
    "SyncBackfillChunkPayload",
    "SyncIncrementalPayload",
    "SyncSlackPayload",
    "TaskEnvelope",
]

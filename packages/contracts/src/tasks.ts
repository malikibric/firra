import type { z } from "zod";
import {
  assessmentReplayPayload,
  briefMeetingsPayload,
  diagnosticsLlmProbePayload,
  diagnosticsPingPayload,
  ingestUploadPayload,
  memoryRebuildPayload,
  needsUpdatePayload,
  onboardingSuggestPayload,
  processRecordsPayload,
  routinesRunPayload,
  signalsEvaluatePayload,
  synthCardsPayload,
  syncBackfillChunkPayload,
  syncIncrementalPayload,
  syncSlackPayload,
  vaultRehydratePayload,
} from "./generated/index.js";

/** Payload schema per task type. Keep in sync with the worker's handler registry. */
export const taskPayloadSchemas = {
  "diagnostics.ping": diagnosticsPingPayload,
  "diagnostics.llm_probe": diagnosticsLlmProbePayload,
  "sync.backfill_chunk": syncBackfillChunkPayload,
  "sync.incremental": syncIncrementalPayload,
  "sync.slack": syncSlackPayload,
  "onboarding.suggest": onboardingSuggestPayload,
  "ingest.upload": ingestUploadPayload,
  "process.records": processRecordsPayload,
  "signals.evaluate": signalsEvaluatePayload,
  "memory.rebuild": memoryRebuildPayload,
  "needs.update": needsUpdatePayload,
  "assessment.replay": assessmentReplayPayload,
  "synth.cards": synthCardsPayload,
  "brief.meetings": briefMeetingsPayload,
  "routines.run": routinesRunPayload,
  "vault.rehydrate": vaultRehydratePayload,
} as const;

export type TaskType = keyof typeof taskPayloadSchemas;
export type TaskPayload<T extends TaskType> = z.infer<(typeof taskPayloadSchemas)[T]>;
/** What callers pass before validation (fields with defaults may be omitted). */
export type TaskPayloadInput<T extends TaskType> = z.input<(typeof taskPayloadSchemas)[T]>;

import { z } from "zod";
import { metadataInputs } from "./clients.js";
import { isoDate, riskState, expansionState, evidenceStrength, clientStatus } from "./common.js";

const nullableDate = isoDate.nullable();
const driverBasics = z.object({
  type: z.string().max(60),
  family: z.string().max(40).nullable(),
  severity: z.enum(["low", "medium", "high"]),
  occurred_at: nullableDate,
  primary: z.boolean(),
  evidence_ids: z.array(z.uuid()),
});
export const replayDriver = z.discriminatedUnion("basis", [
  driverBasics.extend({ basis: z.literal("evidence") }),
  driverBasics.extend({
    basis: z.literal("metadata"),
    evidence_ids: z.array(z.uuid()).max(0),
    inputs: metadataInputs.extend({
      share: z.number().optional(),
      overdue_days: z.number().optional(),
      due_date: isoDate.optional(),
      departed_at: z.iso.datetime({ offset: true }).optional(),
      first_seen_at: z.iso.datetime({ offset: true }).optional(),
    }),
  }),
]);
export const replayNarrative = z.object({
  status: clientStatus,
  end_date: nullableDate,
  end_kind: z.enum(["notice", "churn"]).nullable(),
  detection_at: nullableDate,
  detection_state: riskState.nullable(),
  confirmed_at: nullableDate,
  lead_days: z.number().int().nullable(),
  confirmed_lead_days: z.number().int().nullable(),
  max_state: riskState.nullable(),
  weeks_elevated: z.number().int().nonnegative(),
  transitions: z.array(
    z.object({
      as_of: isoDate,
      from_state: riskState.nullable(),
      to_state: riskState,
      play: z.string().max(40),
      strength: evidenceStrength,
      new_drivers: z.array(replayDriver),
      active_drivers: z.array(replayDriver),
      caveats: z.array(z.string().max(60)),
      days_before_end: z.number().int().nullable(),
      only_ended: z.boolean(),
    }),
  ),
  visible_at_detection: z.array(replayDriver),
  detection_timing: z.array(replayDriver),
  detection_play: z.string().max(40).nullable(),
  caveats: z.array(z.string().max(60)),
  last_as_of: nullableDate,
  last_state: riskState.nullable(),
  unconfirmed_at_end: z.boolean(),
  events: z.array(
    z.object({
      as_of: isoDate,
      kind: z.enum([
        "notice",
        "churn",
        "first_known",
        "new_driver",
        "new_evidence",
        "severity_changed",
        "ended",
      ]),
      driver: replayDriver.nullable(),
      old_severity: z.enum(["low", "medium", "high"]).nullable(),
      grouping_ids: z.array(z.uuid()),
    }),
  ),
});
export const replayTotals = z.object({
  losses: z.number().int().nonnegative(),
  losses_detected_30d: z.number().int().nonnegative(),
  median_lead_days: z.number().nullable(),
  retained: z.number().int().nonnegative(),
  retained_with_elevated: z.number().int().nonnegative(),
  false_alarm_client_weeks_per_10_clients_per_month: z.number().nonnegative(),
  flicker: z.number().int().nonnegative(),
});
export const replayRun = z.object({
  id: z.uuid(),
  window_start: isoDate,
  window_end: isoDate,
  step_days: z.number().int().positive(),
  status: z.enum(["queued", "running", "succeeded", "failed"]),
  finished_at: z.iso.datetime({ offset: true }).nullable(),
  totals: replayTotals.nullable(),
  narrated: z.boolean(),
});
export const replayRunsResponse = z.object({
  generated_at: z.iso.datetime(),
  items: z.array(replayRun).max(500),
});
export const replayClientResponse = z.object({
  generated_at: z.iso.datetime(),
  run_id: z.uuid(),
  client_id: z.uuid(),
  version: z.literal(1),
  narrative: replayNarrative,
  weeks: z.array(
    z.object({
      as_of: isoDate,
      risk: riskState,
      expansion: expansionState,
      play: z.string().max(40).nullable(),
      strength: evidenceStrength,
    }),
  ),
  evidence_ids: z.array(z.uuid()),
});
export type ReplayRun = z.infer<typeof replayRun>;
export type ReplayClient = z.infer<typeof replayClientResponse>;
export type ReplayNarrative = z.infer<typeof replayNarrative>;

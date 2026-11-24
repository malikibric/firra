import { z } from "zod";
import {
  clientPolicy,
  clientStatus,
  evidenceStrength,
  expansionState,
  isoDate,
  riskState,
} from "./common.js";

const evidenceIds = z.array(z.uuid()).max(5);
export const clientListStatus = z.enum(["active", "churned", "all"]);
export type ClientListStatus = z.infer<typeof clientListStatus>;
export const clientBasics = z.object({
  id: z.uuid(),
  name: z.string().max(200),
  status: clientStatus,
  policy: clientPolicy,
  mrr: z.number().nonnegative().nullable(),
  currency: z
    .string()
    .regex(/^[A-Z]{3}$/)
    .nullable(),
  renewal_date: isoDate.nullable(),
  notice_period_days: z.number().int().nonnegative().nullable(),
});
export const clientAssessmentSummary = z.object({
  risk_state: riskState,
  state_since: isoDate,
  expansion_state: expansionState,
  play: z.string().max(40).nullable(),
  as_of: isoDate,
});
export const clientListItem = clientBasics.extend({
  assessment: clientAssessmentSummary.nullable(),
});
export type ClientListItem = z.infer<typeof clientListItem>;
export const clientsResponse = z.object({
  generated_at: z.iso.datetime(),
  items: z.array(clientListItem).max(500),
});

// Metadata inputs contain counters, dates and ids, never arbitrary message text.
export const metadataInputs = z.object({
  affected: z.number().optional(),
  total: z.number().optional(),
  initiated_30: z.number().optional(),
  baseline_per_30: z.number().optional(),
  authors_60: z.number().optional(),
  baseline_authors_per_60: z.number().optional(),
  messages_30: z.number().optional(),
  recent_median_h: z.number().optional(),
  base_median_h: z.number().optional(),
  recent_n: z.number().optional(),
  base_n: z.number().optional(),
  mean_90: z.number().optional(),
  base_mean: z.number().optional(),
  person_id: z.uuid().optional(),
  last_message_at: z.iso.datetime({ offset: true }).optional(),
  messages_31_180: z.number().optional(),
  renewal_date: isoDate.optional(),
  notice_deadline: isoDate.nullable().optional(),
  days_to_notice: z.number().int().nullable().optional(),
  contract_start: isoDate.optional(),
  days: z.number().int().optional(),
});
const driverBasics = z.object({
  type: z.string().max(60),
  family: z.string().max(40).nullable(),
  severity: z.enum(["low", "medium", "high"]),
  occurred_at: z.iso.datetime({ offset: true }).nullable(),
  primary: z.boolean(),
  evidence_ids: z.array(z.uuid()),
});
export const clientDriver = z.discriminatedUnion("basis", [
  driverBasics.extend({ basis: z.literal("evidence") }),
  driverBasics.extend({ basis: z.literal("metadata"), inputs: metadataInputs }),
]);
export const clientAssessment = clientAssessmentSummary.extend({
  previous_risk_state: riskState.nullable(),
  strength: evidenceStrength,
  caveats: z.array(z.object({ code: z.string().max(60), detail: z.string() })),
  drivers: z.array(clientDriver),
});
export const clientStateChange = z.object({ as_of: isoDate, from: riskState, to: riskState });
export type ClientStateChange = z.infer<typeof clientStateChange>;
export const clientDetail = clientBasics.extend({
  assessment: clientAssessment.nullable(),
  state_history: z.array(clientStateChange).max(20),
  stakeholders: z.array(
    z.object({
      display_name: z.string().nullable(),
      role_title: z.string().nullable(),
      is_decision_maker: z.boolean(),
      is_champion: z.boolean().optional(),
      last_seen_at: z.iso.datetime({ offset: true }).nullable(),
    }),
  ),
  /** What Firra does not know about this client yet (client card checklist). */
  unknowns: z.array(z.object({ code: z.string().max(60), detail: z.string().max(300) })).optional(),
  commitments: z.array(
    z.object({
      owner_side: z.enum(["client", "firm"]),
      text: z.string().max(500),
      due_date: isoDate.nullable(),
      status: z.enum(["open", "late"]),
      evidence_ids: evidenceIds.optional(),
    }),
  ),
  goals: z.array(
    z.object({
      text: z.string().max(500),
      metric: z.string().nullable(),
      stated_at: z.iso.datetime({ offset: true }),
      evidence_ids: evidenceIds.optional(),
    }),
  ),
  needs: z.array(
    z.object({
      topic: z.string().nullable(),
      mention_count: z.number().int().nonnegative(),
      thread_count: z.number().int().nonnegative(),
      first_seen_at: z.iso.datetime({ offset: true }),
      last_seen_at: z.iso.datetime({ offset: true }),
      evidence_ids: evidenceIds.optional(),
    }),
  ),
  outcomes: z.array(
    z.object({
      event: z.enum(["renewed", "price_increase", "downgrade", "expanded", "churned", "referral"]),
      occurred_at: isoDate,
      notice_at: isoDate.nullable(),
      first_known_at: isoDate.nullable(),
      amount_change: z.number().nullable(),
      currency: z
        .string()
        .regex(/^[A-Z]{3}$/)
        .nullable(),
    }),
  ),
});
export type ClientDetail = z.infer<typeof clientDetail>;
export const clientDetailResponse = z.object({
  generated_at: z.iso.datetime(),
  client: clientDetail,
});

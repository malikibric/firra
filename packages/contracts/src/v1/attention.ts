import { z } from "zod";
import {
  clientPolicy,
  clientStatus,
  evidenceStrength,
  expansionState,
  isoDate,
  riskState,
} from "./common.js";

/** A driver behind an assessment, without free text; clients map `type` to wording. */
export const attentionDriver = z.object({
  type: z.string().max(60),
  family: z.string().max(40).nullable(),
  severity: z.enum(["low", "medium", "high"]),
  basis: z.enum(["evidence", "metadata"]),
  occurred_at: isoDate.nullable(),
  primary: z.boolean(),
  evidence_count: z.number().int().nonnegative(),
});

export const attentionItem = z.object({
  client_id: z.uuid(),
  name: z.string(),
  status: clientStatus,
  policy: clientPolicy,
  as_of: isoDate,
  risk_state: riskState,
  previous_risk_state: riskState.nullable(),
  state_since: isoDate,
  expansion_state: expansionState,
  strength: evidenceStrength,
  play: z.string().max(40).nullable(),
  days_to_notice: z.number().int().nullable(),
  drivers: z.array(attentionDriver).max(2),
});
export type AttentionItem = z.infer<typeof attentionItem>;

export const attentionResponse = z.object({
  generated_at: z.iso.datetime(),
  items: z.array(attentionItem).max(150),
});
export type AttentionResponse = z.infer<typeof attentionResponse>;

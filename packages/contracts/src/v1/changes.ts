import { z } from "zod";
import { isoDate } from "./common.js";

export const changeKind = z.enum([
  "risk_state_changed",
  "expansion_state_changed",
  "signal_opened",
  "signal_escalated",
  "signal_resolved",
]);
export type ChangeKind = z.infer<typeof changeKind>;

/**
 * One change in a client's picture, newest first. No free text: `from`/`to` are state names for the
 * two `*_state_changed` kinds and null otherwise; signal changes carry the signal `type` (clients
 * map it to wording) and how many evidence rows back it. Quotes are fetched through the evidence
 * endpoints, so a `metadata_only` client never exposes text here.
 */
export const changeItem = z.object({
  client_id: z.uuid(),
  client_name: z.string().max(200),
  as_of: isoDate,
  kind: changeKind,
  from: z.string().max(20).nullable(),
  to: z.string().max(20).nullable(),
  signal_type: z.string().max(60).nullable(),
  family: z.string().max(40).nullable(),
  severity: z.enum(["low", "medium", "high"]).nullable(),
  evidence_count: z.number().int().nonnegative(),
});
export type ChangeItem = z.infer<typeof changeItem>;

export const changesResponse = z.object({
  generated_at: z.iso.datetime(),
  since: isoDate,
  items: z.array(changeItem).max(300),
});
export type ChangesResponse = z.infer<typeof changesResponse>;

/** Query of `GET /v1/workspaces/:id/changes`: `since` defaults to 7 days ago, at most 31 days back. */
export const changesQuery = z.object({ since: isoDate.optional() });

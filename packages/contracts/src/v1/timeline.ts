import { z } from "zod";
import { riskState } from "./common.js";

const base = { id: z.string(), occurred_at: z.iso.datetime({ offset: true }), client_id: z.uuid() };
export const timelineEvent = z.discriminatedUnion("kind", [
  z.object({ ...base, kind: z.literal("state_change"), from: riskState, to: riskState }),
  z.object({
    ...base,
    kind: z.literal("signal"),
    type: z.string(),
    change: z.enum(["opened", "severity_changed", "reopened", "resolved"]),
    severity: z.enum(["low", "medium", "high"]),
    evidence_ids: z.array(z.uuid()),
  }),
  z.object({
    ...base,
    kind: z.literal("commitment"),
    change: z.enum(["created", "due", "closed"]),
    owner_side: z.enum(["firm", "client"]),
    status: z.enum(["open", "late", "done", "cancelled"]),
  }),
  z.object({
    ...base,
    kind: z.literal("outcome"),
    event: z.enum(["churned", "renewed", "expanded", "price_increase", "downgrade", "referral"]),
  }),
]);
export const evidenceItem = z.object({
  id: z.uuid(),
  quote: z.string().refine((s) => {
    const n = s.trim().split(/\s+/u).length;
    return n >= 3 && n <= 25;
  }),
  locator: z.record(z.string(), z.unknown()),
  deep_link: z.string().nullable(),
  occurred_at: z.iso.datetime({ offset: true }),
  author_name: z.string().nullable(),
  author_role: z.string().nullable(),
  subject: z.string().max(120).nullable(),
  source_ref: z.string().length(8),
  client_id: z.uuid(),
});
export const timelineResponse = z.object({
  items: z.array(timelineEvent),
  next_cursor: z.string().nullable(),
});
export const evidenceResponse = z.object({
  items: z.array(evidenceItem),
  next_cursor: z.string().nullable(),
});
export type TimelineEvent = z.infer<typeof timelineEvent>;
export type EvidenceItem = z.infer<typeof evidenceItem>;
export type TimelinePage = z.infer<typeof timelineResponse>;
export type EvidencePage = z.infer<typeof evidenceResponse>;

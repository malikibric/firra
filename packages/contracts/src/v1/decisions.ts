import { z } from "zod";
import { evidenceStrength, isoDate } from "./common.js";

export const decisionStatus = z.enum(["new", "done", "dismissed", "all"]);
export type DecisionStatus = z.infer<typeof decisionStatus>;
export const feedbackVerdict = z.enum(["useful", "already_known", "wrong", "not_now"]);
export const feedbackAction = z.enum(["taken", "planned", "none"]);
export const feedbackRequest = z.object({
  verdict: feedbackVerdict,
  action: feedbackAction.nullish(),
  note: z.string().max(1000).nullish(),
});
export type FeedbackRequest = z.infer<typeof feedbackRequest>;
export const outcomeRequest = z
  .object({
    event: z.enum(["renewed", "price_increase", "downgrade", "expanded", "churned", "referral"]),
    occurred_at: z.iso.date(),
    notice_at: z.iso.date().nullish(),
    first_known_at: z.iso.date().nullish(),
    amount_change: z.number().min(-9999999999.99).max(9999999999.99).nullish(),
    currency: z
      .string()
      .regex(/^[A-Z]{3}$/)
      .nullish(),
    note: z.string().max(1000).nullish(),
  })
  .refine((r) => (r.amount_change == null) === (r.currency == null), {
    message: "Amount and currency must be supplied together.",
  })
  .refine(
    (r) =>
      (!r.notice_at || r.notice_at <= r.occurred_at) &&
      (!r.first_known_at || r.first_known_at <= r.occurred_at),
    {
      message: "Notice and first-known dates must not follow the outcome.",
    },
  );
export type OutcomeRequest = z.infer<typeof outcomeRequest>;
export const mutationResponse = z.object({ id: z.uuid() });
export const cardImportance = z.enum(["fyi", "attention", "important"]);
export const decisionKind = z.enum([
  "risk",
  "margin",
  "pricing",
  "growth",
  "external",
  "commitment",
  "state_change",
  "commitment_watch",
  "expansion",
]);
export type DecisionKind = z.infer<typeof decisionKind>;
/** Optional filters of `GET /v1/workspaces/:id/decisions` and of the MCP `get_decisions` tool. */
export const decisionFilters = z.object({
  importance: cardImportance.optional(),
  kind: decisionKind.optional(),
});
export type DecisionFilters = z.infer<typeof decisionFilters>;
export const decisionItem = z.object({
  id: z.uuid(),
  client_id: z.uuid(),
  client_name: z.string().max(200),
  week: isoDate,
  kind: decisionKind,
  /** Set by routines (docs/plans/e-routines.md); null on the older weekly cards. */
  importance: cardImportance.nullish(),
  surfaced_on: isoDate.nullish(),
  title: z.string(),
  body: z.string(),
  play: z.string(),
  strength: evidenceStrength,
  priority: z.number().int(),
  status: z.enum(["new", "done", "dismissed"]),
  created_at: z.iso.datetime({ offset: true }),
  evidence_ids: z.array(z.uuid()),
  feedback: z
    .object({
      verdict: feedbackVerdict,
      action: feedbackAction.nullable(),
      note: z.string().max(1000).nullable(),
    })
    .nullable(),
});
export const decisionsResponse = z.object({
  items: z.array(decisionItem).max(100),
  next_cursor: z.string().nullable(),
});
export type DecisionsPage = z.infer<typeof decisionsResponse>;

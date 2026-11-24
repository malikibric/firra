import { z } from "zod";
import { clientPolicy, isoDate } from "./common.js";

/** What a question is about: the item behind it, so the app can show why it is asked. */
export const meetingQuestion = z.object({
  text: z.string().min(1).max(400),
  basis: z.object({
    kind: z.enum(["complaint", "commitment", "stakeholder", "renewal", "unknown"]),
    ref: z.string().max(80),
  }),
});

const evidenceIds = z.array(z.uuid()).max(5);

/**
 * One pre-meeting brief (F1). Free-text fields are one-sentence statements already stored by the
 * worker; quotes are fetched through the evidence endpoints by id. A `metadata_only` client's brief
 * carries counts, dates and checklist codes only: the lists of statements are empty.
 */
export const meetingBriefItem = z.object({
  id: z.uuid(),
  calendar_event_id: z.uuid(),
  client_id: z.uuid(),
  client_name: z.string(),
  policy: clientPolicy,
  starts_at: z.iso.datetime(),
  basis: z.enum(["evidence", "metadata"]),
  since_last_meeting: z.object({
    records: z.number().int().nonnegative(),
    last_held_at: z.iso.datetime().nullable(),
  }),
  open_commitments: z
    .array(
      z.object({
        side: z.enum(["firm", "client"]),
        text: z.string().max(500),
        due: isoDate.nullable(),
        late: z.boolean(),
        evidence_ids: evidenceIds,
      }),
    )
    .max(5),
  unresolved_complaints: z
    .array(
      z.object({
        subtype: z.string().max(40),
        text: z.string().max(500),
        evidence_ids: evidenceIds,
      }),
    )
    .max(5),
  stakeholder_changes: z
    .array(z.object({ text: z.string().max(500), evidence_ids: evidenceIds }))
    .max(5),
  renewal: z.object({ date: isoDate, days_to_notice: z.number().int().nullable() }).nullable(),
  unknowns: z.array(z.object({ code: z.string().max(60), detail: z.string().max(200) })).max(10),
  questions: z.array(meetingQuestion).max(3),
});
export type MeetingBriefItem = z.infer<typeof meetingBriefItem>;

export const meetingsResponse = z.object({
  generated_at: z.iso.datetime(),
  items: z.array(meetingBriefItem).max(10),
});
export type MeetingsResponse = z.infer<typeof meetingsResponse>;

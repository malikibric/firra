import type { MeetingBriefItem } from "@firra/contracts";
import { z } from "zod";

export interface BriefRow {
  id: string;
  calendar_event_id: string;
  client_id: string;
  starts_at: string;
  basis: "evidence" | "metadata";
  content: unknown;
}

export interface MeetingEventRow {
  id: string;
  status: string;
  starts_at: string;
}

export interface MeetingClientRow {
  id: string;
  name: string;
  status: string;
  policy: string;
}

const MAX_BRIEFS = 10;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DATE = /^\d{4}-\d{2}-\d{2}$/;

const ids = z.array(z.string()).catch([]);
const text = z.string().min(1).max(500);
/** The worker's `facts.v1` content. Anything that does not fit is dropped, never passed on. */
const content = z.object({
  since_last_meeting: z
    .object({ records: z.number().int().nonnegative(), last_held_at: z.string().nullish() })
    .catch({ records: 0, last_held_at: null }),
  open_commitments: z
    .array(
      z.object({
        side: z.enum(["firm", "client"]),
        text,
        due: z.string().nullish(),
        late: z.boolean().catch(false),
        evidence_ids: ids,
      }),
    )
    .catch([]),
  unresolved_complaints: z
    .array(z.object({ subtype: z.string().max(40).catch("other"), text, evidence_ids: ids }))
    .catch([]),
  stakeholder_changes: z.array(z.object({ text, evidence_ids: ids })).catch([]),
  renewal: z
    .object({ date: z.string(), days_to_notice: z.number().int().nullish() })
    .nullish()
    .catch(null),
  unknowns: z.array(z.object({ code: z.string().max(60), detail: z.string().max(200) })).catch([]),
  questions: z
    .array(
      z.object({
        text: z.string().min(1).max(400),
        basis: z.object({
          kind: z.enum(["complaint", "commitment", "stakeholder", "renewal", "unknown"]),
          ref: z.string().max(80),
        }),
      }),
    )
    .catch([]),
});

const iso = (value: string): string | null => {
  const time = Date.parse(value);
  return Number.isNaN(time) ? null : new Date(time).toISOString();
};
const cleanIds = (values: string[]) => values.filter((v) => UUID.test(v)).slice(0, 5);
const day = (value: string | null | undefined) => (value && DATE.test(value) ? value : null);

/**
 * Briefs for meetings that have not started and are still scheduled at the time the brief was
 * built (a rescheduled or cancelled meeting no longer matches), soonest first. A client that is not
 * `full` gets counts, dates and checklist codes only, whatever the stored row says.
 */
export function buildMeetings(
  briefs: BriefRow[],
  events: MeetingEventRow[],
  clients: MeetingClientRow[],
  now: Date,
): MeetingBriefItem[] {
  const eventById = new Map(events.map((e) => [e.id, e]));
  const clientById = new Map(clients.map((c) => [c.id, c]));
  const items: MeetingBriefItem[] = [];
  for (const brief of briefs) {
    const startsAt = iso(brief.starts_at);
    const event = eventById.get(brief.calendar_event_id);
    const client = clientById.get(brief.client_id);
    if (!startsAt || !event || !client) continue;
    if (event.status !== "scheduled" || iso(event.starts_at) !== startsAt) continue;
    if (
      client.status !== "active" ||
      (client.policy !== "full" && client.policy !== "metadata_only")
    ) {
      continue;
    }
    if (Date.parse(startsAt) <= now.getTime()) continue;
    const parsed = content.safeParse(brief.content);
    if (!parsed.success) continue;
    const c = parsed.data;
    const full = client.policy === "full";
    items.push({
      id: brief.id,
      calendar_event_id: brief.calendar_event_id,
      client_id: brief.client_id,
      client_name: client.name,
      policy: client.policy,
      starts_at: startsAt,
      basis: full ? brief.basis : "metadata",
      since_last_meeting: {
        records: c.since_last_meeting.records,
        last_held_at: c.since_last_meeting.last_held_at
          ? iso(c.since_last_meeting.last_held_at)
          : null,
      },
      open_commitments: full
        ? c.open_commitments.slice(0, 5).map((x) => ({
            side: x.side,
            text: x.text,
            due: day(x.due),
            late: x.late,
            evidence_ids: cleanIds(x.evidence_ids),
          }))
        : [],
      unresolved_complaints: full
        ? c.unresolved_complaints.slice(0, 5).map((x) => ({
            subtype: x.subtype,
            text: x.text,
            evidence_ids: cleanIds(x.evidence_ids),
          }))
        : [],
      stakeholder_changes: full
        ? c.stakeholder_changes
            .slice(0, 5)
            .map((x) => ({ text: x.text, evidence_ids: cleanIds(x.evidence_ids) }))
        : [],
      renewal:
        c.renewal && day(c.renewal.date)
          ? { date: c.renewal.date, days_to_notice: c.renewal.days_to_notice ?? null }
          : null,
      unknowns: c.unknowns.slice(0, 10),
      questions: c.questions
        .filter((q) => full || q.basis.kind === "renewal" || q.basis.kind === "unknown")
        .slice(0, 3),
    });
  }
  return items
    .sort((a, b) => a.starts_at.localeCompare(b.starts_at) || a.id.localeCompare(b.id))
    .slice(0, MAX_BRIEFS);
}

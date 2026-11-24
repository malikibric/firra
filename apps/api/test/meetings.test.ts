import { describe, expect, it } from "vitest";
import {
  buildMeetings,
  type BriefRow,
  type MeetingClientRow,
  type MeetingEventRow,
} from "../src/lib/meetings.js";

const NOW = new Date("2026-10-03T12:00:00Z");
const EV = "44444444-4444-4444-8444-444444444444";
const CL = "22222222-2222-4222-8222-222222222222";
const EVID = "55555555-5555-4555-8555-555555555555";
const FULL_CONTENT = {
  since_last_meeting: { records: 14, last_held_at: "2026-09-20T10:00:00+00:00" },
  open_commitments: [
    {
      side: "firm",
      text: "Send the report",
      due: "2026-10-10",
      late: false,
      evidence_ids: [EVID, "x"],
    },
  ],
  unresolved_complaints: [{ subtype: "delay", text: "Reports are late", evidence_ids: [EVID] }],
  stakeholder_changes: [{ text: "A new VP joined the threads", evidence_ids: [EVID] }],
  renewal: { date: "2026-12-01", days_to_notice: 41 },
  unknowns: [{ code: "no_decision_maker", detail: "No decision maker is identified." }],
  questions: [
    { text: "Is the delay resolved?", basis: { kind: "complaint", ref: EVID } },
    { text: "Who decides on renewal?", basis: { kind: "unknown", ref: "no_decision_maker" } },
  ],
};

const brief = (over: Partial<BriefRow> = {}): BriefRow => ({
  id: "33333333-3333-4333-8333-333333333333",
  calendar_event_id: EV,
  client_id: CL,
  starts_at: "2026-10-03T14:00:00+00:00",
  basis: "evidence",
  content: FULL_CONTENT,
  ...over,
});
const event = (over: Partial<MeetingEventRow> = {}): MeetingEventRow => ({
  id: EV,
  status: "scheduled",
  starts_at: "2026-10-03T14:00:00+00:00",
  ...over,
});
const client = (over: Partial<MeetingClientRow> = {}): MeetingClientRow => ({
  id: CL,
  name: "Redfern",
  status: "active",
  policy: "full",
  ...over,
});

describe("buildMeetings", () => {
  it("shapes a full-policy brief and keeps only well-formed evidence ids", () => {
    const [item] = buildMeetings([brief()], [event()], [client()], NOW);
    expect(item?.starts_at).toBe("2026-10-03T14:00:00.000Z");
    expect(item?.since_last_meeting.last_held_at).toBe("2026-09-20T10:00:00.000Z");
    expect(item?.open_commitments[0]).toMatchObject({
      text: "Send the report",
      evidence_ids: [EVID],
    });
    expect(item?.unresolved_complaints).toHaveLength(1);
    expect(item?.renewal).toEqual({ date: "2026-12-01", days_to_notice: 41 });
    expect(item?.questions).toHaveLength(2);
    expect(item?.basis).toBe("evidence");
  });

  it("never passes statements or evidence for a metadata_only client, whatever the row holds", () => {
    const [item] = buildMeetings([brief()], [event()], [client({ policy: "metadata_only" })], NOW);
    expect(item?.policy).toBe("metadata_only");
    expect(item?.basis).toBe("metadata");
    expect(item?.open_commitments).toEqual([]);
    expect(item?.unresolved_complaints).toEqual([]);
    expect(item?.stakeholder_changes).toEqual([]);
    expect(item?.questions.map((q) => q.basis.kind)).toEqual(["unknown"]);
    expect(JSON.stringify(item)).not.toContain("Send the report");
    expect(JSON.stringify(item)).not.toContain(EVID);
  });

  it("drops meetings that were cancelled, moved, started, restricted or are for other clients", () => {
    const cases: [BriefRow, MeetingEventRow, MeetingClientRow][] = [
      [brief(), event({ status: "cancelled" }), client()],
      [brief(), event({ starts_at: "2026-10-03T15:00:00+00:00" }), client()],
      [
        brief({ starts_at: "2026-10-03T11:00:00+00:00" }),
        event({ starts_at: "2026-10-03T11:00:00+00:00" }),
        client(),
      ],
      [brief(), event(), client({ status: "churned" })],
      [brief(), event(), client({ policy: "excluded" })],
    ];
    for (const [b, e, c] of cases) expect(buildMeetings([b], [e], [c], NOW)).toEqual([]);
    expect(buildMeetings([brief()], [event()], [], NOW)).toEqual([]);
    expect(buildMeetings([brief()], [], [client()], NOW)).toEqual([]);
  });

  it("skips a brief whose content is not an object and tolerates broken sections", () => {
    expect(buildMeetings([brief({ content: "nope" })], [event()], [client()], NOW)).toEqual([]);
    const [item] = buildMeetings(
      [brief({ content: { open_commitments: "bad", questions: [{ text: "" }] } })],
      [event()],
      [client()],
      NOW,
    );
    expect(item?.open_commitments).toEqual([]);
    expect(item?.questions).toEqual([]);
    expect(item?.since_last_meeting.records).toBe(0);
  });

  it("orders by start time and returns at most 10", () => {
    const many = Array.from({ length: 12 }, (_, i) => {
      const id = `${i.toString(16).padStart(8, "0")}-1111-4111-8111-111111111111`;
      const startsAt = new Date(NOW.getTime() + (12 - i) * 3_600_000).toISOString();
      return {
        brief: brief({ id, calendar_event_id: id, starts_at: startsAt }),
        event: event({ id, starts_at: startsAt }),
      };
    });
    const items = buildMeetings(
      many.map((m) => m.brief),
      many.map((m) => m.event),
      [client()],
      NOW,
    );
    expect(items).toHaveLength(10);
    expect(items.map((i) => i.starts_at)).toEqual([...items.map((i) => i.starts_at)].sort());
  });
});

import { afterEach, describe, expect, it, vi } from "vitest";
import { SupabaseReadStore } from "../src/lib/read-store.js";
import { page, parsePage } from "../src/lib/read-page.js";
import { CLIENT, WS, required } from "./client-fixtures.js";
const eid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const sourceId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const evidence = {
  id: eid,
  client_id: CLIENT.id,
  quote: "Synthetic verified evidence quote",
  locator: { start: 0, end: 33 },
  deep_link: null,
};
const source = {
  id: sourceId,
  occurred_at: "2026-09-30T00:00:00+00:00",
  metadata: { subject: "  " + "x".repeat(140) + "  ", private: "PRIVATE BODY" },
  person: { side: "client", display_name: "Synthetic stakeholder", role_title: "CEO" },
  evidence: [evidence],
};
const caller = { token: "synthetic-token", userId: crypto.randomUUID() };
const now = new Date("2026-10-01T12:00:00Z");
const store = new SupabaseReadStore("http://localhost:54321", "fixture-key");
afterEach(() => vi.unstubAllGlobals());
function fake(reply: (url: URL) => unknown, status = 200) {
  const calls: URL[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string | Request | URL, init?: RequestInit) => {
      expect(new Headers(init?.headers).get("authorization")).toBe(`Bearer ${caller.token}`);
      const url = new URL(input instanceof Request ? input.url : input.toString());
      calls.push(url);
      expect(url.searchParams.get("workspace_id")).toBe(`eq.${WS}`);
      return Promise.resolve(
        new Response(JSON.stringify(reply(url)), {
          status,
          headers: { "content-type": "application/json" },
        }),
      );
    }),
  );
  return calls;
}
const query = required(parsePage("50"));
describe("Timeline and evidence caller store", () => {
  it("merges every event type, skips unchanged live states, selects no bodies or notes", async () => {
    const calls = fake((url) => {
      const table = url.pathname.split("/").at(-1);
      if (table === "client") return { policy: "full" };
      if (table === "account_assessment")
        return [
          { id: eid, as_of: "2026-09-30", previous_risk_state: "stable", risk_state: "watch" },
          { id: sourceId, as_of: "2026-09-29", previous_risk_state: "watch", risk_state: "watch" },
        ];
      if (table === "signal_event")
        return [
          {
            id: eid,
            as_of: source.occurred_at,
            change: "opened",
            severity: "medium",
            signal: {
              type: "delivery_complaint",
              basis: "evidence",
              signal_evidence: [{ evidence_id: eid }],
            },
          },
        ];
      if (table === "outcome") return [{ id: eid, occurred_at: "2026-09-30", event: "renewed" }];
      if (table === "commitment")
        return [
          {
            id: eid,
            stated_at: "2026-09-28T00:00:00Z",
            due_date: "2026-09-29",
            closed_at: "2026-09-30T00:00:00Z",
            owner_side: "firm",
            status: "done",
          },
        ];
      throw new Error("unexpected table");
    });
    const result = required(await store.timeline(caller, WS, CLIENT.id, query, now));
    expect(result.items).toHaveLength(6);
    expect(result.next_cursor).toBeNull();
    expect(result.items.filter((e) => e.kind === "commitment").map((e) => e.change)).toEqual([
      "closed",
      "due",
      "created",
    ]);
    expect(result.items.find((e) => e.kind === "signal")).toMatchObject({ evidence_ids: [eid] });
    for (const url of calls) {
      if (!url.pathname.endsWith("/client"))
        expect(url.searchParams.get("client_id")).toBe(`eq.${CLIENT.id}`);
      expect(url.searchParams.get("select")).not.toMatch(/body_clean|note|text|inputs/);
    }
    expect(
      required(calls.find((u) => u.pathname.endsWith("/account_assessment"))).searchParams.get(
        "run_kind",
      ),
    ).toBe("eq.live");
  });
  it("continues through unchanged assessment pages using keysets rather than offsets", async () => {
    const calls = fake((url) => {
      if (url.pathname.endsWith("/client")) return { policy: "metadata_only" };
      if (!url.pathname.endsWith("/account_assessment")) return [];
      if (!url.searchParams.has("or"))
        return Array.from({ length: 100 }, (_, i) => ({
          id: `${i.toString(16).padStart(8, "0")}-aaaa-4aaa-8aaa-aaaaaaaaaaaa`,
          as_of: "2026-09-30",
          previous_risk_state: "stable",
          risk_state: "stable",
        }));
      return [{ id: eid, as_of: "2026-09-29", previous_risk_state: "stable", risk_state: "watch" }];
    });
    const result = required(await store.timeline(caller, WS, CLIENT.id, query, now));
    expect(result.items).toHaveLength(1);
    expect(calls.some((u) => u.searchParams.has("or"))).toBe(true);
    expect(calls.some((u) => u.searchParams.has("offset"))).toBe(false);
  });
  it("enforces metadata policy without reading body-derived tables", async () => {
    const calls = fake((url) =>
      url.pathname.endsWith("/client")
        ? { policy: "metadata_only" }
        : url.pathname.endsWith("/signal_event")
          ? [
              {
                id: eid,
                as_of: source.occurred_at,
                change: "resolved",
                severity: "low",
                signal: {
                  type: "reduced_engagement",
                  basis: "metadata",
                  signal_evidence: [{ evidence_id: eid }],
                },
              },
            ]
          : [],
    );
    expect(await store.clientEvidence(caller, WS, CLIENT.id, query, now)).toEqual({
      items: [],
      next_cursor: null,
    });
    const result = required(await store.timeline(caller, WS, CLIENT.id, query, now));
    expect(result.items[0]).toMatchObject({ evidence_ids: [] });
    expect(calls.some((u) => /commitment|source_record/.test(u.pathname))).toBe(false);
    expect(
      required(calls.find((u) => u.pathname.endsWith("/signal_event"))).searchParams.get(
        "signal.basis",
      ),
    ).toBe("eq.metadata");
  });
  it("returns null for hidden/foreign clients and evidence", async () => {
    const calls = fake(() => []);
    expect(await store.timeline(caller, WS, CLIENT.id, query, now)).toBeNull();
    expect(await store.clientEvidence(caller, WS, CLIENT.id, query, now)).toBeNull();
    expect(await store.evidence(caller, WS, eid, now)).toBeNull();
    expect(calls).toHaveLength(3);
    expect(required(calls.at(-1)).pathname.endsWith("/evidence")).toBe(true);
  });
  it("hides evidence of a client that is not on full policy (checked separately, no embed)", async () => {
    const calls = fake((url) =>
      url.pathname.endsWith("/client")
        ? { policy: "metadata_only" }
        : { ...evidence, source_record: { ...source } },
    );
    expect(await store.evidence(caller, WS, eid, now)).toBeNull();
    expect(calls.map((u) => u.pathname.split("/").at(-1))).toEqual(["evidence", "client"]);
    expect(calls[0]?.searchParams.get("select")).not.toContain("client!");
  });
  it("uses source date, trims subjects and removes firm-side authors and arbitrary metadata", async () => {
    let side = "client";
    const calls = fake((url) =>
      url.pathname.endsWith("/client")
        ? { policy: "full" }
        : url.pathname.endsWith("/evidence")
          ? {
              ...evidence,
              client: { policy: "full" },
              source_record: { ...source, person: { ...source.person, side } },
            }
          : [{ ...source, person: { ...source.person, side } }],
    );
    const list = required(await store.clientEvidence(caller, WS, CLIENT.id, query, now));
    const single = required(await store.evidence(caller, WS, eid, now));
    expect(single).toEqual(list.items[0]);
    expect(single.subject).toHaveLength(120);
    expect(single.source_ref).toBe("bbbbbbbb");
    expect(single.author_role).toBe("CEO");
    side = "firm";
    expect(await store.evidence(caller, WS, eid, now)).toMatchObject({
      author_name: null,
      author_role: null,
    });
    expect(JSON.stringify(single)).not.toContain("PRIVATE BODY");
    for (const url of calls) expect(url.searchParams.get("select")).not.toContain("body_clean");
  });
  it("reads all sources sharing a boundary date before slicing evidence UUID ties", async () => {
    const calls = fake((url) => {
      if (url.pathname.endsWith("/client")) return { policy: "full" };
      if (!url.searchParams.has("or"))
        return Array.from({ length: 100 }, (_, i) => ({
          ...source,
          id: `${i.toString(16).padStart(8, "0")}-bbbb-4bbb-8bbb-bbbbbbbbbbbb`,
          evidence: [
            { ...evidence, id: `${i.toString(16).padStart(8, "0")}-aaaa-4aaa-8aaa-aaaaaaaaaaaa` },
          ],
        }));
      return [
        { ...source, evidence: [{ ...evidence, id: "ffffffff-aaaa-4aaa-8aaa-aaaaaaaaaaaa" }] },
      ];
    });
    const first = required(
      await store.clientEvidence(
        caller,
        WS,
        CLIENT.id,
        required(parsePage("1", undefined, true)),
        now,
      ),
    );
    expect(first.items[0]?.id).toBe("ffffffff-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
    expect(first.next_cursor).not.toBeNull();
    expect(calls).toHaveLength(3);
    const q = required(parsePage("1", required(first.next_cursor), true));
    expect(page(first.items, q).items).toEqual([]);
  });
  it("restricts evidence UUIDs only at the cursor date and bounds each embedded source", async () => {
    const cursor = Buffer.from(`2026-09-30T00:00:00.000000Z|${eid}`).toString("base64url");
    const calls = fake((url) => (url.pathname.endsWith("/client") ? { policy: "full" } : []));
    expect(
      await store.clientEvidence(
        caller,
        WS,
        CLIENT.id,
        required(parsePage("1", cursor, true)),
        now,
      ),
    ).toEqual({ items: [], next_cursor: null });
    const same = required(calls[1]);
    const older = required(calls[2]);
    expect(same.searchParams.get("occurred_at")).toBe("eq.2026-09-30T00:00:00.000000Z");
    expect(same.searchParams.get("evidence.id")).toBe(`lt.${eid}`);
    expect(older.searchParams.get("occurred_at")).toBe("lt.2026-09-30T00:00:00.000000Z");
    expect(older.searchParams.has("evidence.id")).toBe(false);
    for (const url of [same, older]) {
      expect(url.searchParams.get("evidence.limit")).toBe("2");
      expect(url.searchParams.get("evidence.order")).toBe("id.desc");
    }
  });
  it("throws fixed errors on failed reads", async () => {
    fake(() => ({ code: "42501", message: "PRIVATE BODY" }), 403);
    await expect(store.timeline(caller, WS, CLIENT.id, query, now)).rejects.toThrow(
      "client read failed",
    );
    await expect(store.evidence(caller, WS, eid, now)).rejects.toThrow("evidence read failed");
  });
});

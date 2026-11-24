import { afterEach, expect, it, vi } from "vitest";
import { SupabaseReadStore } from "../src/lib/read-store.js";
import { WS, CLIENT, required } from "./client-fixtures.js";
import { RUN, RUN_ID, NARRATIVE, EVIDENCE_ID } from "./replay-fixtures.js";
const store = new SupabaseReadStore("http://localhost:54321", "fixture");
const caller = { token: "synthetic", userId: crypto.randomUUID() };
const now = new Date("2026-10-01T12:00:00Z");
afterEach(() => vi.unstubAllGlobals());
function fake(reply: (url: URL) => unknown) {
  const calls: URL[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string | Request | URL, init?: RequestInit) => {
      const url = new URL(input instanceof Request ? input.url : input.toString());
      calls.push(url);
      expect(new Headers(init?.headers).get("authorization")).toBe("Bearer synthetic");
      expect(url.searchParams.get("workspace_id")).toBe(`eq.${WS}`);
      return Promise.resolve(
        new Response(JSON.stringify(reply(url)), {
          headers: { "content-type": "application/json" },
        }),
      );
    }),
  );
  return calls;
}
it("lists narrated and legacy runs without reading narrative text", async () => {
  const calls = fake(() => [
    { ...RUN, summary: { totals: RUN.totals }, replay_narrative: [{ version: 1 }] },
    { ...RUN, id: CLIENT.id, summary: null, replay_narrative: [] },
  ]);
  const result = await store.replayRuns(caller, WS, now);
  expect(result.map((r) => r.narrated)).toEqual([true, false]);
  expect(result[1]?.totals).toBeNull();
  expect(calls[0]?.searchParams.get("select")).toContain("replay_narrative(version)");
});
it("reads stored judgements and every weekly row, deduplicates evidence ids", async () => {
  const calls = fake((url) => {
    if (url.pathname.endsWith("/client")) return { policy: "full" };
    if (url.pathname.endsWith("/replay_narrative"))
      return { version: 1, narrative: NARRATIVE, replay_run: { status: "succeeded" } };
    return [
      {
        as_of: "2026-09-30",
        risk_state: "high",
        expansion_state: "none",
        play: "fix_first",
        strength: "strong",
      },
    ];
  });
  const result = required(await store.replayClient(caller, WS, RUN_ID, CLIENT.id, now));
  expect(result.narrative).toEqual(NARRATIVE);
  expect(result.evidence_ids).toEqual([EVIDENCE_ID]);
  expect(result.weeks[0]?.risk).toBe("high");
  for (const url of calls.filter((u) => !u.pathname.endsWith("/client"))) {
    expect(url.searchParams.get("replay_run_id")).toBe(`eq.${RUN_ID}`);
    expect(url.searchParams.get("client_id")).toBe(`eq.${CLIENT.id}`);
  }
});
it("refuses foreign/excluded clients, absent narratives and body-derived metadata narratives", async () => {
  for (const scenario of ["hidden", "absent", "metadata"]) {
    const calls = fake((url) =>
      url.pathname.endsWith("/client")
        ? scenario === "hidden"
          ? null
          : { policy: scenario === "metadata" ? "metadata_only" : "full" }
        : scenario === "absent"
          ? null
          : { version: 1, narrative: NARRATIVE },
    );
    expect(await store.replayClient(caller, WS, RUN_ID, CLIENT.id, now)).toBeNull();
    expect(calls.some((u) => u.pathname.endsWith("/account_assessment"))).toBe(false);
  }
});
it("serves metadata drivers with typed counters and no evidence", async () => {
  const narrative = {
    ...NARRATIVE,
    visible_at_detection: [
      {
        type: "reduced_engagement",
        family: "engagement",
        severity: "high",
        basis: "metadata",
        inputs: { messages_30: 0, baseline_per_30: 12, text: "PRIVATE BODY" },
        occurred_at: "2026-09-23",
        evidence_ids: [],
        primary: true,
      },
    ],
  };
  fake((url) =>
    url.pathname.endsWith("/client")
      ? { policy: "metadata_only" }
      : url.pathname.endsWith("/replay_narrative")
        ? { narrative }
        : [],
  );
  const result = required(await store.replayClient(caller, WS, RUN_ID, CLIENT.id, now));
  expect(result.evidence_ids).toEqual([]);
  expect(JSON.stringify(result)).not.toContain("PRIVATE BODY");
});

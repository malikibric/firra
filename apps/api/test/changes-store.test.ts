import { afterEach, describe, expect, it, vi } from "vitest";
import { SupabaseReadStore } from "../src/lib/read-store.js";
import { CLIENT, WS, required } from "./client-fixtures.js";
import { NOW, SINCE, assessment, event } from "./changes-fixtures.js";
const caller = { token: "fixture-token", userId: crypto.randomUUID() };
const store = new SupabaseReadStore("http://localhost:54321", "fixture-key");
afterEach(() => vi.unstubAllGlobals());
function fake(reply: (url: URL) => unknown, status = 200) {
  const calls: URL[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string | Request | URL, init?: RequestInit) => {
      const url = new URL(input instanceof Request ? input.url : input.toString());
      calls.push(url);
      expect(url.searchParams.get("workspace_id")).toBe(`eq.${WS}`);
      expect(new Headers(init?.headers).get("authorization")).toBe(`Bearer ${caller.token}`);
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
function data(url: URL): unknown[] {
  if (url.pathname.endsWith("/client"))
    return [
      {
        ...CLIENT,
        account_assessment: [assessment({ as_of: "2026-09-01", previous_risk_state: null })],
      },
    ];
  if (url.pathname.endsWith("/account_assessment"))
    return [assessment({ expansion_state: "strong" })];
  if (url.pathname.endsWith("/signal_event"))
    return [event({ change: "severity_changed", severity: "high" })];
  const baseline = event({ as_of: "2026-09-25T00:00:00Z" });
  return [{ ...baseline.signal, signal_event: [baseline] }];
}
describe("caller-scoped changes store", () => {
  it("loads live baselines and current events, reads no text, and retains boundary escalations", async () => {
    const calls = fake(data);
    const items = await store.changes(caller, WS, SINCE, NOW);
    expect(items.map((i) => i.kind)).toEqual([
      "risk_state_changed",
      "expansion_state_changed",
      "signal_escalated",
    ]);
    for (const url of calls)
      expect(url.searchParams.get("select")).not.toMatch(/quote|inputs|body|drivers|text/);
    const client = required(calls.find((u) => u.pathname.endsWith("/client")));
    expect(client.searchParams.get("account_assessment.as_of")).toBe(`lt.${SINCE}`);
    expect(client.searchParams.get("account_assessment.run_kind")).toBe("eq.live");
    expect(client.searchParams.get("account_assessment.limit")).toBe("1");
    const signal = required(calls.find((u) => u.pathname.endsWith("/signal")));
    expect(signal.searchParams.get("signal_event.as_of")).toBe(`lt.${SINCE}T00:00:00Z`);
    expect(signal.searchParams.get("signal_event.limit")).toBe("1");
    const assessmentUrl = required(calls.find((u) => u.pathname.endsWith("/account_assessment")));
    expect(assessmentUrl.searchParams.get("run_kind")).toBe("eq.live");
    expect(assessmentUrl.searchParams.getAll("as_of")).toEqual([`gte.${SINCE}`, "lte.2026-10-03"]);
  });
  it("pages through unchanged rows instead of capping input rows before shaping", async () => {
    const calls = fake((url) => {
      if (!url.pathname.endsWith("/account_assessment")) return data(url);
      if (url.searchParams.get("offset") === "0")
        return Array.from({ length: 500 }, () => assessment({ previous_risk_state: "watch" }));
      return [assessment({ as_of: "2026-09-27", expansion_state: "hypothesis" })];
    });
    const items = await store.changes(caller, WS, SINCE, NOW);
    expect(items.some((i) => i.kind === "risk_state_changed" && i.as_of === "2026-09-27")).toBe(
      true,
    );
    expect(calls.filter((u) => u.pathname.endsWith("/account_assessment"))).toHaveLength(2);
  });
  it("fails with a fixed error on query failures", async () => {
    fake(() => ({ code: "42501", message: "PRIVATE BODY" }), 403);
    await expect(store.changes(caller, WS, SINCE, NOW)).rejects.toThrow("client read failed");
  });
});

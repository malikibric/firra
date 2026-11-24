import { required } from "./client-fixtures.js";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SupabaseReadStore } from "../src/lib/read-store.js";
import { ASSESSMENT, CLIENT, PARTS, WS } from "./client-fixtures.js";
const caller = { token: "synthetic-caller-token", userId: crypto.randomUUID() };
const now = new Date("2026-10-01T12:00:00Z");
const store = new SupabaseReadStore("http://localhost:54321", "synthetic-publishable-key");
afterEach(() => vi.unstubAllGlobals());
function fakeFetch(reply: (url: URL) => unknown, status = 200) {
  const calls: {
    url: URL;
    headers: Headers;
  }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(input instanceof Request ? input.url : input.toString());
      calls.push({ url, headers: new Headers(init?.headers) });
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
describe("Supabase client reads", () => {
  it("uses caller authorization, scoped visible clients, per-client latest live assessments and deterministic bounds", async () => {
    const calls = fakeFetch(() => [
      { ...CLIENT, account_assessment: [{ ...ASSESSMENT, as_of: "2025-01-01" }] },
    ]);
    const result = await store.clients(caller, WS, "active", now);
    expect(required(required(result[0]).assessment).as_of).toBe("2025-01-01");
    const { url, headers } = required(calls[0]);
    expect(headers.get("authorization")).toBe(`Bearer ${caller.token}`);
    expect(url.searchParams.get("workspace_id")).toBe(`eq.${WS}`);
    expect(url.searchParams.get("policy")).toBe("in.(full,metadata_only)");
    expect(url.searchParams.get("status")).toBe("eq.active");
    expect(url.searchParams.get("account_assessment.run_kind")).toBe("eq.live");
    expect(url.searchParams.get("account_assessment.limit")).toBe("1");
    expect(url.searchParams.get("account_assessment.order")).toBe("as_of.desc");
    expect(url.searchParams.get("limit")).toBe("500");
    expect(url.searchParams.get("order")).toBe("name.asc,id.asc");
    expect(url.searchParams.get("select")).not.toContain("drivers");
  });
  it("reads churned only or all statuses including prospects and unassessed clients", async () => {
    const calls = fakeFetch(() => [{ ...CLIENT, status: "prospect", account_assessment: [] }]);
    expect(required((await store.clients(caller, WS, "all", now))[0]).assessment).toBeNull();
    expect(required(calls[0]).url.searchParams.has("status")).toBe(false);
    await store.clients(caller, WS, "churned", now);
    expect(required(calls[1]).url.searchParams.get("status")).toBe("eq.churned");
  });
  it("returns null for a hidden or foreign client without querying its data", async () => {
    const calls = fakeFetch(() => []);
    expect(await store.clientDetail(caller, WS, CLIENT.id, now)).toBeNull();
    expect(calls).toHaveLength(1);
    expect(required(calls[0]).url.searchParams.get("id")).toBe(`eq.${CLIENT.id}`);
    expect(required(calls[0]).url.searchParams.get("workspace_id")).toBe(`eq.${WS}`);
  });
  it("skips body-derived tables under metadata-only and scans past unchanged assessment pages", async () => {
    const calls = fakeFetch((url) => {
      const table = url.pathname.split("/").at(-1);
      if (table === "client") return [{ ...CLIENT, policy: "metadata_only" }];
      if (table === "account_assessment") {
        if (required(url.searchParams.get("select")).includes("drivers")) return [ASSESSMENT];
        return url.searchParams.get("offset") === "0"
          ? Array.from({ length: 100 }, () => ({ ...ASSESSMENT, previous_risk_state: "high" }))
          : [{ ...ASSESSMENT, as_of: "2026-01-01" }];
      }
      if (table === "client_card") return [];
      if (table === "person") return PARTS.stakeholders;
      if (table === "outcome") return PARTS.outcomes;
      throw new Error("Unexpected body-derived table read");
    });
    const result = await store.clientDetail(caller, WS, CLIENT.id, now);
    expect(required(result).commitments).toEqual([]);
    expect(required(result).goals).toEqual([]);
    expect(required(result).needs).toEqual([]);
    expect(required(result).state_history).toEqual([
      { as_of: "2026-01-01", from: "watch", to: "high" },
    ]);
    for (const { url, headers } of calls) {
      expect(headers.get("authorization")).toBe(`Bearer ${caller.token}`);
      expect(url.searchParams.get("workspace_id")).toBe(`eq.${WS}`);
      if (!url.pathname.endsWith("/client"))
        expect(url.searchParams.get("client_id")).toBe(`eq.${CLIENT.id}`);
      if (url.pathname.endsWith("/account_assessment"))
        expect(url.searchParams.get("run_kind")).toBe("eq.live");
    }
  });
  it("reads the latest scoped card and bounds each embedded evidence list as caller", async () => {
    const calls = fakeFetch((url) => {
      switch (url.pathname.split("/").at(-1)) {
        case "client":
          return [CLIENT];
        case "account_assessment":
          return [];
        case "person":
          return PARTS.stakeholders;
        case "commitment":
          return PARTS.commitments;
        case "client_goal":
          return PARTS.goals;
        case "latent_need":
          return PARTS.needs;
        case "outcome":
          return PARTS.outcomes;
        case "client_card":
          return [{ content: PARTS.cardContent }];
        default:
          throw new Error("Unexpected table");
      }
    });
    const result = required(await store.clientDetail(caller, WS, CLIENT.id, now));
    expect(result.unknowns).toEqual(PARTS.cardContent.unknowns);
    expect(result.stakeholders[0]?.is_champion).toBe(true);
    for (const [table, join] of [
      ["commitment", "commitment_evidence"],
      ["client_goal", "client_goal_evidence"],
      ["latent_need", "latent_need_evidence"],
    ]) {
      const url = required(calls.find((call) => call.url.pathname.endsWith(`/${table}`))).url;
      expect(url.searchParams.get(`${join}.limit`)).toBe("5");
      expect(url.searchParams.get(`${join}.order`)).toBe("created_at.desc,evidence_id.asc");
      expect(url.searchParams.get("select")).toContain(`${join}(evidence_id,created_at)`);
    }
    const card = required(calls.find((call) => call.url.pathname.endsWith("/client_card"))).url;
    expect(card.searchParams.get("order")).toBe("version.desc");
    expect(card.searchParams.get("limit")).toBe("1");
    for (const { url, headers } of calls) {
      expect(headers.get("authorization")).toBe(`Bearer ${caller.token}`);
      expect(url.searchParams.get("workspace_id")).toBe(`eq.${WS}`);
      if (!url.pathname.endsWith("/client"))
        expect(url.searchParams.get("client_id")).toBe(`eq.${CLIENT.id}`);
    }
  });
  it("fails closed on database errors with no provider or row text in the error", async () => {
    fakeFetch(() => ({ code: "42501", message: "PRIVATE BODY" }), 403);
    await expect(store.clients(caller, WS, "all", now)).rejects.toThrow("client read failed");
    await expect(store.clientDetail(caller, WS, CLIENT.id, now)).rejects.toThrow(
      "client read failed",
    );
  });
});

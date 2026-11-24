import { apiError, decisionsResponse, mutationResponse, outcomeRequest } from "@firra/contracts";
import type { DecisionsPage } from "@firra/contracts";
import { describe, expect, it, vi, afterEach } from "vitest";
import { SupabaseReadStore } from "../src/lib/read-store.js";
import { SupabaseWriteStore, WriteForbiddenError } from "../src/lib/write-store.js";
import type { WriteStore } from "../src/lib/write-store.js";
import { USERS, bearer, testApp } from "./helpers.js";

const WS = "11111111-1111-4111-8111-111111111111";
const ID = "22222222-2222-4222-8222-222222222222";
const OWNER = USERS["token-owner"]?.id ?? "";
const base = `/v1/workspaces/${WS}`;
const card: DecisionsPage["items"][number] = {
  id: ID,
  client_id: ID,
  client_name: "Synthetic client",
  week: "2026-10-01",
  kind: "risk",
  title: "PRIVATE TITLE",
  body: "PRIVATE BODY",
  play: "fix_first",
  strength: "strong",
  priority: 1,
  status: "new",
  created_at: "2026-10-01T00:00:00Z",
  evidence_ids: [ID],
  feedback: null,
};
class FakeWrites implements WriteStore {
  setRoutineEnabled: WriteStore["setRoutineEnabled"] = (...args) => this.record(args);
  calls: unknown[] = [];
  error: Error | null = null;
  feedback: WriteStore["feedback"] = (...args) => this.record(args);
  outcome: WriteStore["outcome"] = (...args) => this.record(args);
  deleteOutcome: WriteStore["deleteOutcome"] = async (...args) => {
    await this.record(args);
  };
  private record(args: unknown[]) {
    this.calls.push(args);
    return this.error ? Promise.reject(this.error) : Promise.resolve(ID);
  }
}
function setup(limit = 1000) {
  const writes = new FakeWrites();
  const store = new SupabaseReadStore("http://localhost:54321", "public-key");
  Object.defineProperty(store, "writes", { value: writes });
  const read = vi.spyOn(store, "decisions").mockResolvedValue({ items: [card], next_cursor: null });
  const t = testApp({ readStore: store, userRateLimitPerMinute: limit });
  t.repo.addMember(WS, OWNER, "owner");
  t.repo.addMember(WS, USERS["token-admin"]?.id ?? "", "admin");
  return { ...t, writes, read };
}
const operations = [
  ["POST", `${base}/decisions/${ID}/feedback`, { verdict: "useful", note: "PRIVATE NOTE" }],
  ["POST", `${base}/clients/${ID}/outcomes`, { event: "churned", occurred_at: "2026-10-01" }],
  ["DELETE", `${base}/outcomes/${ID}`, undefined],
] as const;
const headers = { ...bearer("token-owner"), "content-type": "application/json" };

describe("Decisions read", () => {
  it("validates contracts, passes status/page/caller and audits before returning without row logs", async () => {
    const t = setup();
    const res = await t.app.request(`${base}/decisions?status=all&limit=1`, { headers });
    expect(res.status).toBe(200);
    expect(decisionsResponse.parse(await res.json()).items).toEqual([card]);
    expect(t.read).toHaveBeenCalledWith(
      { token: "token-owner", userId: OWNER },
      WS,
      "all",
      { limit: 1, cursor: null },
      expect.any(Date),
      {},
    );
    expect(t.repo.audit).toEqual([
      expect.objectContaining({ action: "read.decisions", metadata: { count: 1 } }),
    ]);
    expect(t.logs.join()).not.toContain("PRIVATE");
    t.repo.failAudit = true;
    const failed = await t.app.request(`${base}/decisions`, { headers });
    expect(failed.status).toBe(500);
    expect(await failed.text()).not.toContain("PRIVATE");
  });
  it("passes the importance and kind filters to the store and returns the routine fields", async () => {
    const t = setup();
    const routineCard = {
      ...card,
      kind: "state_change" as const,
      importance: "important" as const,
      surfaced_on: "2026-10-05",
    };
    t.read.mockResolvedValue({ items: [routineCard], next_cursor: null });
    const res = await t.app.request(`${base}/decisions?importance=important&kind=state_change`, {
      headers,
    });
    expect(res.status).toBe(200);
    expect(decisionsResponse.parse(await res.json()).items).toEqual([routineCard]);
    expect(t.read).toHaveBeenCalledWith(
      { token: "token-owner", userId: OWNER },
      WS,
      "new",
      { limit: 50, cursor: null },
      expect.any(Date),
      { importance: "important", kind: "state_change" },
    );
  });
  it("rejects invalid pagination and status without reading", async () => {
    const t = setup();
    for (const query of [
      "status=bogus",
      "limit=101",
      "cursor=bogus",
      "importance=urgent",
      "importance=",
      "kind=bogus",
    ])
      expect((await t.app.request(`${base}/decisions?${query}`, { headers })).status).toBe(422);
    expect(t.read).not.toHaveBeenCalled();
    expect(t.repo.audit).toEqual([]);
  });
});
for (const [method, url, body] of operations)
  describe(`${method} ${url}`, () => {
    it("runs as caller, returns the contract, never caches or double audits", async () => {
      const t = setup();
      const res = await t.app.request(url, { method, headers, body: body && JSON.stringify(body) });
      expect(res.status).toBe(method === "DELETE" ? 204 : 200);
      if (method !== "DELETE") expect(mutationResponse.parse(await res.json()).id).toBe(ID);
      expect(res.headers.get("cache-control")).toBe("no-store");
      expect(t.writes.calls[0]).toEqual([
        { token: "token-owner", userId: OWNER },
        WS,
        ID,
        ...(body ? [body] : []),
      ]);
      expect(t.repo.audit).toEqual([]);
      expect(t.logs.join()).not.toContain("PRIVATE");
    });
    it("maps forbidden to an indistinguishable 404 and all other failures to 500", async () => {
      for (const [error, status] of [
        [new WriteForbiddenError(), 404],
        [new Error("PRIVATE ROW"), 500],
      ] as const) {
        const t = setup();
        t.writes.error = error;
        const res = await t.app.request(url, {
          method,
          headers,
          body: body && JSON.stringify(body),
        });
        expect(res.status).toBe(status);
        apiError.parse(await res.json());
        expect(t.logs.join()).not.toContain("PRIVATE");
      }
    });
    it("refuses outsiders, foreign workspaces, invalid ids and missing JSON content type before mutation", async () => {
      const t = setup();
      for (const [target, h, status] of [
        [url, bearer("token-outsider"), 403],
        [url.replace(WS, ID), headers, 403],
        [url.replace(ID, "bad"), headers, 422],
        ...(method === "DELETE" ? [] : ([[url, bearer("token-owner"), 422]] as const)),
      ] as const)
        expect((await t.app.request(target, { method, headers: h })).status).toBe(status);
      expect(t.writes.calls).toEqual([]);
    });
    it("a DELETE needs no content type (it has no body)", async () => {
      if (method !== "DELETE") return;
      const t = setup();
      const res = await t.app.request(url, { method, headers: bearer("token-owner") });
      expect(res.status).toBe(204);
      expect(t.writes.calls).toHaveLength(1);
    });
    it("applies the existing user limit", async () => {
      const t = setup(1);
      const req = { method, headers, body: body && JSON.stringify(body) };
      await t.app.request(url, req);
      expect((await t.app.request(url, req)).status).toBe(429);
      expect(t.writes.calls).toHaveLength(1);
    });
  });
it("validates JSON and outcome constraints before any RPC", async () => {
  const t = setup();
  for (const body of [
    "{",
    "{}",
    JSON.stringify({ verdict: "bad" }),
    JSON.stringify({ verdict: "wrong", note: "x".repeat(1001) }),
  ])
    expect((await t.app.request(operations[0][1], { method: "POST", headers, body })).status).toBe(
      422,
    );
  expect(outcomeRequest.safeParse({ event: "churned", occurred_at: "2026-02-30" }).success).toBe(
    false,
  );
  expect(
    outcomeRequest.safeParse({ event: "expanded", occurred_at: "2026-10-01", amount_change: 1 })
      .success,
  ).toBe(false);
  expect(
    outcomeRequest.safeParse({
      event: "churned",
      occurred_at: "2026-10-01",
      notice_at: "2026-10-02",
    }).success,
  ).toBe(false);
  expect(t.writes.calls).toEqual([]);
});

afterEach(() => vi.unstubAllGlobals());
it("the production RPC adapter uses the caller bearer, checks workspace and sanitizes provider errors", async () => {
  const calls: { url: string; init: RequestInit }[] = [];
  let code: string | null = null;
  let visible = true;
  vi.stubGlobal("fetch", (url: string, init: RequestInit) => {
    calls.push({ url: url, init });
    return Promise.resolve(
      new Response(
        JSON.stringify(
          url.includes("/rpc/")
            ? code
              ? { code, message: "PRIVATE" }
              : ID
            : visible
              ? { id: ID }
              : null,
        ),
        {
          status: code && url.includes("/rpc/") ? 403 : 200,
          headers: { "content-type": "application/json" },
        },
      ),
    );
  });
  const store = new SupabaseWriteStore("http://localhost:54321", "public");
  const caller = { token: "caller-token", userId: OWNER };
  await store.feedback(caller, WS, ID, { verdict: "wrong" });
  expect(calls[0]?.url).toContain(`workspace_id=eq.${WS}`);
  expect(new Headers(calls[1]?.init.headers).get("authorization")).toBe("Bearer caller-token");
  expect(JSON.parse(calls[1]?.init.body as string)).toEqual({
    p_decision_card_id: ID,
    p_verdict: "wrong",
  });
  code = "42501";
  await expect(store.feedback(caller, WS, ID, { verdict: "useful" })).rejects.toBeInstanceOf(
    WriteForbiddenError,
  );
  code = "XX000";
  await expect(
    store.outcome(caller, WS, ID, { event: "renewed", occurred_at: "2026-10-01" }),
  ).rejects.toThrow("outcome write failed");
  code = null;
  visible = false;
  calls.length = 0;
  await expect(store.deleteOutcome(caller, WS, ID)).rejects.toBeInstanceOf(WriteForbiddenError);
  expect(calls).toHaveLength(1);
});

it("production list reads caller-only feedback and evidence links, with stable timestamp/id pagination", async () => {
  const fetched: string[] = [];
  vi.stubGlobal("fetch", (url: string) => {
    fetched.push(url);
    return Promise.resolve(
      new Response(
        JSON.stringify([
          {
            ...card,
            client: { name: card.client_name, policy: "full" },
            facts: [{ basis: "evidence" }],
            decision_evidence: [{ evidence_id: ID }],
            decision_feedback: [{ verdict: "useful", action: null, note: null }],
          },
          {
            ...card,
            id: "11111111-1111-4111-8111-111111111111",
            client: { name: "Metadata", policy: "metadata_only" },
            facts: [{ basis: "metadata" }],
            decision_evidence: [],
            decision_feedback: [],
          },
        ]),
        { headers: { "content-type": "application/json" } },
      ),
    );
  });
  const store = new SupabaseReadStore("http://localhost:54321", "public");
  const result = await store.decisions(
    { token: "token-owner", userId: OWNER },
    WS,
    "all",
    { limit: 1, cursor: null },
    new Date(),
  );
  expect(result.items[0]?.feedback?.verdict).toBe("useful");
  expect(result.items[0]?.evidence_ids).toEqual([ID]);
  expect(Buffer.from(result.next_cursor ?? "", "base64url").toString()).toBe(
    `2026-10-01T00:00:00.000000Z|${ID}`,
  );
  expect(fetched[0]).toContain(`decision_feedback.user_id=eq.${OWNER}`);
  const cursor = { occurred_at: "2026-10-01T00:00:00.000000Z", id: ID };
  const second = await store.decisions(
    { token: "token-owner", userId: OWNER },
    WS,
    "new",
    { limit: 1, cursor },
    new Date(),
  );
  expect(second.items[0]?.client_name).toBe("Metadata");
  expect(second.items[0]?.evidence_ids).toEqual([]);
  expect(fetched[1]).toContain("status=eq.new");
  expect(decodeURIComponent(fetched[1] ?? "")).toContain(
    `created_at.eq.${cursor.occurred_at},id.lt.${ID}`,
  );
});

it("fails closed for an incompatible metadata card instead of exposing its body", async () => {
  vi.stubGlobal("fetch", () =>
    Promise.resolve(
      new Response(
        JSON.stringify([
          {
            ...card,
            client: { name: "Metadata", policy: "metadata_only" },
            facts: [{ basis: "evidence" }],
            decision_evidence: [],
            decision_feedback: [],
          },
        ]),
        { headers: { "content-type": "application/json" } },
      ),
    ),
  );
  const store = new SupabaseReadStore("http://localhost:54321", "public");
  await expect(
    store.decisions(
      { token: "owner", userId: OWNER },
      WS,
      "all",
      { limit: 50, cursor: null },
      new Date(),
    ),
  ).rejects.toThrow("decision policy check failed");
});

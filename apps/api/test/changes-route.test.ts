import { changesResponse } from "@firra/contracts";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SupabaseReadStore } from "../src/lib/read-store.js";
import { buildChanges } from "../src/lib/changes.js";
import { WS, required } from "./client-fixtures.js";
import { NOW, SINCE, clients, event } from "./changes-fixtures.js";
import { USERS, bearer, testApp } from "./helpers.js";

class Store extends SupabaseReadStore {
  override changes = vi.fn(() => Promise.resolve(buildChanges([], [event()], clients, SINCE, NOW)));
}
function setup() {
  const store = new Store("http://localhost", "fixture");
  const t = testApp({ readStore: store });
  t.repo.addMember(WS, required(USERS["token-owner"]).id, "owner");
  t.repo.addMember(WS, required(USERS["token-admin"]).id, "admin");
  return { ...t, store };
}
const url = `/v1/workspaces/${WS}/changes`;
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});
afterEach(() => vi.useRealTimers());
describe("changes route", () => {
  it("validates response, forwards caller and date, audits before returning, and defaults to seven days", async () => {
    const { app, repo, store } = setup();
    const original = repo.writeAudit.bind(repo);
    const order: string[] = [];
    repo.writeAudit = async (entry) => {
      order.push("audit");
      await original(entry);
    };
    const response = await app.request(url, { headers: bearer("token-owner") });
    order.push("response");
    expect(order).toEqual(["audit", "response"]);
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(changesResponse.parse(await response.json()).since).toBe(SINCE);
    expect(store.changes).toHaveBeenCalledWith(
      { token: "token-owner", userId: required(USERS["token-owner"]).id },
      WS,
      SINCE,
      NOW,
    );
    expect(repo.audit[0]).toMatchObject({
      action: "read.changes",
      workspaceId: WS,
      metadata: { count: 1 },
    });
  });
  it("accepts admin and inclusive 31-day/today boundaries", async () => {
    const { app } = setup();
    for (const since of ["2026-09-02", "2026-10-03"])
      expect(
        (await app.request(`${url}?since=${since}`, { headers: bearer("token-admin") })).status,
      ).toBe(200);
  });
  it("refuses anonymous and non-members without touching store or audit", async () => {
    const { app, repo, store } = setup();
    expect((await app.request(url)).status).toBe(401);
    expect((await app.request(url, { headers: bearer("token-outsider") })).status).toBe(403);
    expect(store.changes).not.toHaveBeenCalled();
    expect(repo.audit).toEqual([]);
  });
  it.each(["", "nope", "2026-09-01", "2026-10-04", "2026-09-31", "2026-99-99", "2026-9-30"])(
    "rejects invalid since %s",
    async (since) => {
      const { app, repo, store } = setup();
      const response = await app.request(`${url}?since=${since}`, {
        headers: bearer("token-owner"),
      });
      expect(response.status).toBe(422);
      expect(await response.json()).toMatchObject({ error: "invalid_request" });
      expect(store.changes).not.toHaveBeenCalled();
      expect(repo.audit).toEqual([]);
    },
  );
  it("fails closed on an audit error", async () => {
    const { app, repo } = setup();
    repo.failAudit = true;
    const response = await app.request(url, { headers: bearer("token-owner") });
    expect(response.status).toBe(500);
    expect(await response.text()).not.toContain("delivery_complaint");
  });
  it("rejects malformed store output before auditing or exposing data", async () => {
    const { app, repo, store } = setup();
    store.changes.mockResolvedValue([
      { ...required(buildChanges([], [event()], clients, SINCE, NOW)[0]), evidence_count: -1 },
    ]);
    const response = await app.request(url, { headers: bearer("token-owner") });
    expect(response.status).toBe(500);
    expect(repo.audit).toEqual([]);
  });
});

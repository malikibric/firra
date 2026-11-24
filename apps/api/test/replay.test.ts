import {
  replayClientResponse,
  replayRunsResponse,
  apiError,
  openApiDocument,
} from "@firra/contracts";
import type { ReplayClient } from "@firra/contracts";
import { describe, expect, it } from "vitest";
import { SupabaseReadStore } from "../src/lib/read-store.js";
import { WS, CLIENT, required } from "./client-fixtures.js";
import { RUN, RUN_ID, NARRATIVE, EVIDENCE_ID } from "./replay-fixtures.js";
import { testApp, USERS, bearer } from "./helpers.js";
class Store extends SupabaseReadStore {
  calls: unknown[] = [];
  missing = false;
  fail = false;
  constructor() {
    super("http://localhost:54321", "fixture");
  }
  override replayRuns(...args: Parameters<SupabaseReadStore["replayRuns"]>) {
    this.calls.push(args);
    if (this.fail) throw new Error("PRIVATE NARRATIVE");
    return Promise.resolve([RUN]);
  }
  override replayClient(...args: Parameters<SupabaseReadStore["replayClient"]>) {
    this.calls.push(args);
    if (this.fail) throw new Error("PRIVATE NARRATIVE");
    const result: ReplayClient = {
      generated_at: args[4].toISOString(),
      run_id: args[2],
      client_id: args[3],
      version: 1,
      narrative: NARRATIVE,
      weeks: [],
      evidence_ids: [EVIDENCE_ID],
    };
    return Promise.resolve(this.missing ? null : result);
  }
}
function setup() {
  const store = new Store();
  const t = testApp({ readStore: store });
  t.repo.addMember(WS, required(USERS["token-owner"]).id, "owner");
  t.repo.addMember(WS, required(USERS["token-admin"]).id, "admin");
  return { ...t, store };
}
const root = `/v1/workspaces/${WS}/replay/runs`;
for (const [url, schema, action] of [
  [root, replayRunsResponse, "read.replay_runs"],
  [`${root}/${RUN_ID}/clients/${CLIENT.id}`, replayClientResponse, "read.replay_client"],
] as const)
  describe(action, () => {
    it("validates success, forwards caller ids and audits once before returning", async () => {
      const { app, repo, store, logs } = setup();
      for (const token of ["token-owner", "token-admin"]) {
        const res = await app.request(url, { headers: bearer(token) });
        expect(res.status).toBe(200);
        schema.parse(await res.json());
        expect(res.headers.get("cache-control")).toBe("no-store");
        expect((store.calls.at(-1) as unknown[]).slice(0, 2)).toEqual([
          { token, userId: required(USERS[token]).id },
          WS,
        ]);
      }
      expect(repo.audit).toHaveLength(2);
      expect(repo.audit[0]).toMatchObject({
        action,
        metadata: { count: 1 },
        ...(action === "read.replay_client" ? { targetType: "client", targetId: CLIENT.id } : {}),
      });
      expect(logs.join("\n")).not.toContain("delivery_complaint");
    });
    it("rejects outsiders, contributors and malformed ids before reading or auditing", async () => {
      const { app, repo, store } = setup();
      repo.memberships.delete(`${WS}:${required(USERS["token-owner"]).id}`);
      for (const token of ["token-owner", "token-outsider"]) {
        const res = await app.request(url, { headers: bearer(token) });
        expect(res.status).toBe(403);
        apiError.parse(await res.json());
      }
      const invalid = await app.request(url.replace(WS, "bad-id"), {
        headers: bearer("token-admin"),
      });
      expect(invalid.status).toBe(422);
      expect(store.calls).toHaveLength(0);
      expect(repo.audit).toHaveLength(0);
    });
    it("fails closed for both store and audit failure without row data in logs", async () => {
      const { app, repo, store, logs } = setup();
      for (const failStore of [true, false]) {
        store.fail = failStore;
        repo.failAudit = !failStore;
        const res = await app.request(url, { headers: bearer("token-owner") });
        expect(res.status).toBe(500);
        apiError.parse(await res.json());
      }
      expect(repo.audit).toHaveLength(0);
      expect(logs.join("\n")).not.toMatch(/PRIVATE NARRATIVE|delivery_complaint/);
    });
    it("returns 503 when reads are unavailable", async () => {
      const { app, repo } = testApp();
      repo.addMember(WS, required(USERS["token-owner"]).id, "owner");
      const res = await app.request(url, { headers: bearer("token-owner") });
      expect(res.status).toBe(503);
      expect(repo.audit).toHaveLength(0);
    });
  });
it("returns an unaudited 404 for an absent narrative", async () => {
  const { app, repo, store } = setup();
  store.missing = true;
  const res = await app.request(`${root}/${RUN_ID}/clients/${CLIENT.id}`, {
    headers: bearer("token-owner"),
  });
  expect(res.status).toBe(404);
  apiError.parse(await res.json());
  expect(repo.audit).toHaveLength(0);
});
it("publishes replay paths and error models in OpenAPI", () => {
  const text = JSON.stringify(openApiDocument("test"));
  expect(text).toContain("getReplayRuns");
  expect(text).toContain("getReplayClient");
  expect(text).toContain("ReplayClientResponse");
});

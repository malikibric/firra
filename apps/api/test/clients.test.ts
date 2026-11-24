import { required } from "./client-fixtures.js";
import { apiError, clientDetailResponse, clientsResponse } from "@firra/contracts";
import type { ClientListStatus } from "@firra/contracts";
import { describe, expect, it } from "vitest";
import { buildClientDetail, buildClientListItem } from "../src/lib/clients.js";
import type { ReadCaller, ReadStore } from "../src/lib/read-store.js";
import { USERS, bearer, testApp } from "./helpers.js";
import { ASSESSMENT, CLIENT, PARTS, WS } from "./client-fixtures.js";
class FakeStore implements ReadStore {
  routines(): ReturnType<ReadStore["routines"]> {
    return Promise.resolve([]);
  }
  changes(): ReturnType<ReadStore["changes"]> {
    return Promise.resolve([]);
  }
  decisions(): ReturnType<ReadStore["decisions"]> {
    return Promise.resolve({ items: [], next_cursor: null });
  }
  replayRuns(): ReturnType<ReadStore["replayRuns"]> {
    return Promise.resolve([]);
  }
  replayClient(): ReturnType<ReadStore["replayClient"]> {
    return Promise.resolve(null);
  }
  timeline(): ReturnType<ReadStore["timeline"]> {
    return Promise.resolve(null);
  }
  clientEvidence(): ReturnType<ReadStore["clientEvidence"]> {
    return Promise.resolve(null);
  }
  evidence(): ReturnType<ReadStore["evidence"]> {
    return Promise.resolve(null);
  }
  calls: unknown[] = [];
  missing = false;
  fail = false;
  metadata = false;
  upcomingMeetings(): ReturnType<ReadStore["upcomingMeetings"]> {
    return Promise.resolve([]);
  }
  attention(): ReturnType<ReadStore["attention"]> {
    return Promise.resolve([]);
  }
  clients(
    caller: ReadCaller,
    workspaceId: string,
    status: ClientListStatus,
    now: Date,
  ): ReturnType<ReadStore["clients"]> {
    this.calls.push({ caller, workspaceId, status, now });
    if (this.fail) throw new Error("PRIVATE BODY");
    return Promise.resolve([
      buildClientListItem(
        { ...CLIENT, policy: this.metadata ? "metadata_only" : "full" },
        ASSESSMENT,
      ),
    ]);
  }
  clientDetail(
    caller: ReadCaller,
    workspaceId: string,
    clientId: string,
    now: Date,
  ): ReturnType<ReadStore["clientDetail"]> {
    this.calls.push({ caller, workspaceId, clientId, now });
    if (this.fail) throw new Error("PRIVATE BODY");
    return Promise.resolve(
      this.missing
        ? null
        : buildClientDetail(
            { ...CLIENT, policy: this.metadata ? "metadata_only" : "full" },
            ASSESSMENT,
            [ASSESSMENT],
            PARTS,
          ),
    );
  }
}
function setup(store: FakeStore | null = new FakeStore(), limit = 1000) {
  const t = testApp({
    readStore: store,
    userRateLimitPerMinute: limit,
    verifyUser: (token) =>
      Promise.resolve(
        token === "token-contributor"
          ? { id: "44444444-4444-4444-8444-444444444444", email: "contributor@example.com" }
          : (USERS[token] ?? null),
      ),
  });
  t.repo.addMember(WS, required(USERS["token-owner"]).id, "owner");
  t.repo.addMember(WS, required(USERS["token-admin"]).id, "admin");
  return { ...t, store };
}
const list = `/v1/workspaces/${WS}/clients`;
const detail = `${list}/${CLIENT.id}`;
for (const [label, url, schema, action] of [
  ["list", list, clientsResponse, "read.clients"],
  ["detail", detail, clientDetailResponse, "read.client_detail"],
] as const) {
  describe(`Client ${label} route`, () => {
    it("returns a contract-valid success as caller, auditing once without logging rows", async () => {
      const { app, repo, store, logs } = setup();
      const res = await app.request(url, {
        headers: { ...bearer("token-owner"), "user-agent": "SyntheticTest/1" },
      });
      expect(res.status).toBe(200);
      const body: unknown = await res.json();
      schema.parse(body);
      if (label === "detail") {
        const client = clientDetailResponse.parse(body).client;
        expect(client.stakeholders[0]?.is_champion).toBe(true);
        expect(client.unknowns).toEqual(PARTS.cardContent.unknowns);
        expect(client.commitments[0]?.evidence_ids).toHaveLength(1);
        expect(client.goals[0]?.evidence_ids).toHaveLength(1);
        expect(client.needs[0]?.evidence_ids).toHaveLength(1);
      }
      expect(res.headers.get("cache-control")).toBe("no-store");
      expect(res.headers.get("x-request-id")).toMatch(/^[0-9a-f-]{36}$/);
      expect(required(store).calls[0]).toMatchObject({
        caller: { token: "token-owner", userId: required(USERS["token-owner"]).id },
        workspaceId: WS,
      });
      expect(repo.audit).toHaveLength(1);
      expect(repo.audit[0]).toMatchObject({
        action,
        actorId: required(USERS["token-owner"]).id,
        workspaceId: WS,
        metadata: { count: 1 },
        userAgent: "SyntheticTest/1",
      });
      if (label === "detail")
        expect(repo.audit[0]).toMatchObject({ targetType: "client", targetId: CLIENT.id });
      else expect(required(repo.audit[0]).targetId).toBeUndefined();
      expect(logs.join("\n")).not.toContain(CLIENT.name);
      expect(logs.join("\n")).not.toContain("PRIVATE BODY");
    });
    it("allows admins; refuses contributor, outsider and foreign workspace before reading", async () => {
      const { app, repo, store } = setup();
      const ok = await app.request(url, { headers: bearer("token-admin") });
      expect(ok.status).toBe(200);
      schema.parse(await ok.json());
      for (const token of ["token-outsider", "token-contributor"]) {
        const res = await app.request(url, { headers: bearer(token) });
        expect(res.status).toBe(403);
        apiError.parse(await res.json());
      }
      const foreign = await app.request(url.replace(WS, crypto.randomUUID()), {
        headers: bearer("token-owner"),
      });
      expect(foreign.status).toBe(403);
      apiError.parse(await foreign.json());
      expect(required(store).calls).toHaveLength(1);
      expect(repo.audit).toHaveLength(1);
    });
    it("rejects malformed ids and unauthenticated calls with the error contract", async () => {
      const { app, repo, store } = setup();
      for (const headers of [{}, bearer("invalid")]) {
        const res = await app.request(url, { headers });
        expect(res.status).toBe(401);
        expect(apiError.parse(await res.json()).error).toBe("unauthorized");
      }
      const res = await app.request(url.replace(WS, "bad"), { headers: bearer("token-owner") });
      expect(res.status).toBe(422);
      expect(apiError.parse(await res.json()).error).toBe("invalid_request");
      expect(required(store).calls).toHaveLength(0);
      expect(repo.audit).toHaveLength(0);
    });
    it("fails closed on audit and read errors, without exposing row data", async () => {
      for (const failure of ["audit", "read"]) {
        const { app, repo, store, logs } = setup();
        repo.failAudit = failure === "audit";
        required(store).fail = failure === "read";
        const res = await app.request(url, { headers: bearer("token-owner") });
        expect(res.status).toBe(500);
        expect(apiError.parse(await res.json()).error).toBe("internal_error");
        expect(repo.audit).toHaveLength(0);
        expect(logs.join("\n")).not.toContain("PRIVATE BODY");
      }
    });
    it("returns 503 without a store and 429 after the user limit", async () => {
      const absent = setup(null);
      const unavailable = await absent.app.request(url, { headers: bearer("token-owner") });
      expect(unavailable.status).toBe(503);
      apiError.parse(await unavailable.json());
      expect(absent.repo.audit).toHaveLength(0);
      const { app, repo } = setup(new FakeStore(), 1);
      const first = await app.request(url, { headers: bearer("token-owner") });
      expect(first.status).toBe(200);
      schema.parse(await first.json());
      const res = await app.request(url, { headers: bearer("token-owner") });
      expect(res.status).toBe(429);
      apiError.parse(await res.json());
      expect(repo.audit).toHaveLength(1);
    });
    it("returns a metadata-only assessment without message-derived text", async () => {
      const { app, store } = setup();
      required(store).metadata = true;
      const res = await app.request(url, { headers: bearer("token-owner") });
      expect(res.status).toBe(200);
      const body: unknown = await res.json();
      schema.parse(body);
      expect(JSON.stringify(body)).not.toContain("PRIVATE BODY");
      if (label === "detail") {
        const client = clientDetailResponse.parse(body).client;
        expect(client.commitments).toEqual([]);
        expect(client.goals).toEqual([]);
        expect(client.needs).toEqual([]);
        expect(required(client.assessment).drivers.map((d) => d.basis)).toEqual(["metadata"]);
      }
    });
  });
}
describe("Client route validation", () => {
  it("passes the default and valid status filters; rejects invalid filters before reading", async () => {
    const { app, store, repo } = setup();
    for (const suffix of ["", "?status=active", "?status=churned", "?status=all"]) {
      const res = await app.request(list + suffix, { headers: bearer("token-owner") });
      expect(res.status).toBe(200);
      clientsResponse.parse(await res.json());
    }
    expect(required(store).calls).toMatchObject([
      { status: "active" },
      { status: "active" },
      { status: "churned" },
      { status: "all" },
    ]);
    const bad = await app.request(list + "?status=prospect", { headers: bearer("token-owner") });
    expect(bad.status).toBe(422);
    apiError.parse(await bad.json());
    expect(repo.audit).toHaveLength(4);
  });
  it("returns indistinguishable unaudited 404s for clients hidden by the store", async () => {
    const { app, store, repo } = setup();
    required(store).missing = true;
    const res = await app.request(detail, { headers: bearer("token-owner") });
    expect(res.status).toBe(404);
    expect(apiError.parse(await res.json()).error).toBe("not_found");
    expect(repo.audit).toHaveLength(0);
    const invalid = await app.request(`${list}/bad`, { headers: bearer("token-owner") });
    expect(invalid.status).toBe(422);
    apiError.parse(await invalid.json());
    expect(required(store).calls).toHaveLength(1);
  });
});

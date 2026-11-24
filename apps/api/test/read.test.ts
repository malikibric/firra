import { attentionResponse, meetingsResponse, openApiDocument } from "@firra/contracts";
import { describe, expect, it } from "vitest";
import type { AttentionItem, MeetingBriefItem } from "@firra/contracts";
import type { ReadCaller, ReadStore } from "../src/lib/read-store.js";
import { USERS, bearer, testApp } from "./helpers.js";

const WS = "11111111-1111-4111-8111-111111111111";
const OWNER = USERS["token-owner"]?.id ?? "";
const ADMIN = USERS["token-admin"]?.id ?? "";
const ITEM: AttentionItem = {
  client_id: "22222222-2222-4222-8222-222222222222",
  name: "Redfern",
  status: "active",
  policy: "full",
  as_of: "2026-10-01",
  risk_state: "high",
  previous_risk_state: "elevated",
  state_since: "2026-09-25",
  expansion_state: "none",
  strength: "moderate",
  play: "fix_first",
  days_to_notice: 24,
  drivers: [
    {
      type: "delivery_complaint",
      family: "delivery",
      severity: "high",
      basis: "evidence",
      occurred_at: "2026-09-30",
      primary: true,
      evidence_count: 1,
    },
  ],
};

const MEETING: MeetingBriefItem = {
  id: "33333333-3333-4333-8333-333333333333",
  calendar_event_id: "44444444-4444-4444-8444-444444444444",
  client_id: ITEM.client_id,
  client_name: "Redfern",
  policy: "full",
  starts_at: "2026-10-03T14:00:00.000Z",
  basis: "metadata",
  since_last_meeting: { records: 3, last_held_at: null },
  open_commitments: [],
  unresolved_complaints: [],
  stakeholder_changes: [],
  renewal: null,
  unknowns: [{ code: "no_decision_maker", detail: "No decision maker is identified." }],
  questions: [
    {
      text: "Who signs off the roadmap now?",
      basis: { kind: "unknown", ref: "no_decision_maker" },
    },
  ],
};

class FakeStore implements ReadStore {
  routines(): ReturnType<ReadStore["routines"]> {
    return Promise.resolve([]);
  }
  changes(): ReturnType<ReadStore["changes"]> {
    return Promise.resolve([]);
  }
  meetingCalls: { caller: ReadCaller; workspaceId: string }[] = [];
  upcomingMeetings(caller: ReadCaller, workspaceId: string): Promise<MeetingBriefItem[]> {
    this.meetingCalls.push({ caller, workspaceId });
    return Promise.resolve([MEETING]);
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
  clients(): ReturnType<ReadStore["clients"]> {
    return Promise.resolve([]);
  }
  clientDetail(): ReturnType<ReadStore["clientDetail"]> {
    return Promise.resolve(null);
  }
  calls: { caller: ReadCaller; workspaceId: string }[] = [];
  attention(caller: ReadCaller, workspaceId: string): Promise<AttentionItem[]> {
    this.calls.push({ caller, workspaceId });
    return Promise.resolve([ITEM]);
  }
}

function setup(store: ReadStore | null = new FakeStore()) {
  const t = testApp({ readStore: store });
  t.repo.addMember(WS, OWNER, "owner");
  t.repo.addMember(WS, ADMIN, "admin");
  return { ...t, store };
}
const url = `/v1/workspaces/${WS}/attention`;

describe("GET /v1/workspaces/:id/attention", () => {
  it("returns the attention list as the caller and audits the read", async () => {
    const { app, repo, store } = setup();
    const res = await app.request(url, {
      headers: { ...bearer("token-owner"), "user-agent": "FirraTest/1" },
    });
    expect(res.status).toBe(200);
    expect(res.headers.get("cache-control")).toBe("no-store");
    expect(res.headers.get("x-request-id")).toMatch(/^[0-9a-f-]{36}$/);
    const body = attentionResponse.parse(await res.json());
    expect(body.items).toEqual([ITEM]);
    expect((store as FakeStore).calls[0]).toEqual({
      caller: { token: "token-owner", userId: OWNER },
      workspaceId: WS,
    });
    expect(repo.audit).toEqual([
      expect.objectContaining({
        workspaceId: WS,
        actorId: OWNER,
        action: "read.attention",
        userAgent: "FirraTest/1",
        metadata: { count: 1 },
      }),
    ]);
  });

  it("allows admins and refuses everyone else without touching data or audit", async () => {
    const { app, repo, store } = setup();
    expect((await app.request(url, { headers: bearer("token-admin") })).status).toBe(200);
    for (const headers of [bearer("token-outsider"), {}, bearer("nope")]) {
      const res = await app.request(url, { headers });
      expect([401, 403]).toContain(res.status);
    }
    const foreign = await app.request(`/v1/workspaces/${crypto.randomUUID()}/attention`, {
      headers: bearer("token-owner"),
    });
    expect(foreign.status).toBe(403);
    expect((store as FakeStore).calls).toHaveLength(1);
    expect(repo.audit).toHaveLength(1);
  });

  it("fails closed when the audit row cannot be written: no data in the response", async () => {
    const { app, repo } = setup();
    repo.failAudit = true;
    const res = await app.request(url, { headers: bearer("token-owner") });
    expect(res.status).toBe(500);
    expect(await res.text()).not.toContain("Redfern");
  });

  it("answers 503 reads_not_configured with the error model when no read store exists", async () => {
    const { app, repo } = setup(null);
    const res = await app.request(url, { headers: bearer("token-owner") });
    expect(res.status).toBe(503);
    expect(await res.json()).toEqual({
      error: "reads_not_configured",
      message: expect.any(String) as string,
      request_id: res.headers.get("x-request-id"),
    });
    expect(repo.audit).toHaveLength(0);
  });

  it("reports a failing store to Sentry once and answers with the error model, no row data", async () => {
    const seen: unknown[] = [];
    class Broken extends FakeStore {
      override attention(): Promise<AttentionItem[]> {
        return Promise.reject(new Error("PRIVATE ROW DATA"));
      }
    }
    const t = testApp({ readStore: new Broken(), captureException: (e) => seen.push(e) });
    t.repo.addMember(WS, OWNER, "owner");
    const res = await t.app.request(url, { headers: bearer("token-owner") });
    expect(res.status).toBe(500);
    const text = await res.text();
    expect(text).not.toContain("PRIVATE ROW DATA");
    expect(JSON.parse(text)).toEqual({
      error: "internal_error",
      message: expect.any(String) as string,
      request_id: res.headers.get("x-request-id"),
    });
    expect(seen).toHaveLength(1);
    expect(t.repo.audit).toHaveLength(0);
  });

  it("rate limits per user, not per address", async () => {
    const t = testApp({ readStore: new FakeStore(), userRateLimitPerMinute: 2 });
    t.repo.addMember(WS, OWNER, "owner");
    t.repo.addMember(WS, ADMIN, "admin");
    const call = (token: string) => t.app.request(url, { headers: bearer(token) });
    expect((await call("token-owner")).status).toBe(200);
    expect((await call("token-owner")).status).toBe(200);
    expect((await call("token-owner")).status).toBe(429);
    expect((await call("token-admin")).status).toBe(200);
  });

  it("never logs row data", async () => {
    const { app, logs } = setup();
    await app.request(url, { headers: bearer("token-owner") });
    expect(logs.join("\n")).not.toContain("Redfern");
  });
});

describe("GET /v1/openapi.json", () => {
  it("requires a user and serves the generated document", async () => {
    const { app } = setup();
    expect((await app.request("/v1/openapi.json")).status).toBe(401);
    const res = await app.request("/v1/openapi.json", { headers: bearer("token-outsider") });
    expect(res.status).toBe(200);
    const doc = (await res.json()) as { paths: Record<string, unknown> };
    expect(doc).toEqual(openApiDocument("test"));
    expect(Object.keys(doc.paths).sort()).toEqual(
      [
        "/vault/keep",
        "/vault/records",
        "/vault/ack",
        "/vault/refetch",
        "/invites/accept",
        "/workspaces",
        "/workspaces/{workspaceId}/audit",
        "/workspaces/{workspaceId}/clients/{clientId}/domains",
        "/workspaces/{workspaceId}/clients/{clientId}/policy",
        "/workspaces/{workspaceId}/connections",
        "/workspaces/{workspaceId}/connections/{connectionId}",
        "/workspaces/{workspaceId}/contributors",
        "/workspaces/{workspaceId}/contributors/{contributorId}",
        "/workspaces/{workspaceId}/invites",
        "/workspaces/{workspaceId}/invites/{inviteId}",
        "/workspaces/{workspaceId}/members",
        "/workspaces/{workspaceId}/members/{userId}",
        "/workspaces/{workspaceId}/services",
        "/workspaces/{workspaceId}/services/{serviceId}",
        "/workspaces/{workspaceId}/setup",
        "/workspaces/{workspaceId}/setup/clients",
        "/workspaces/{workspaceId}/attention",
        "/workspaces/{workspaceId}/changes",
        "/workspaces/{workspaceId}/meetings/upcoming",
        "/workspaces/{workspaceId}/clients",
        "/workspaces/{workspaceId}/clients/{clientId}",
        "/workspaces/{workspaceId}/clients/{clientId}/evidence",
        "/workspaces/{workspaceId}/clients/{clientId}/timeline",
        "/workspaces/{workspaceId}/routines",
        "/workspaces/{workspaceId}/routines/{kind}",
        "/workspaces/{workspaceId}/decisions",
        "/workspaces/{workspaceId}/decisions/{cardId}/feedback",
        "/workspaces/{workspaceId}/clients/{clientId}/outcomes",
        "/workspaces/{workspaceId}/outcomes/{outcomeId}",
        "/workspaces/{workspaceId}/evidence/{evidenceId}",
        "/workspaces/{workspaceId}/replay/runs",
        "/workspaces/{workspaceId}/replay/runs/{runId}/clients/{clientId}",
      ].sort(),
    );
  });
});

describe("GET /v1/workspaces/:id/meetings/upcoming", () => {
  const meetingsUrl = `/v1/workspaces/${WS}/meetings/upcoming`;

  it("returns the briefs as the caller and audits the read", async () => {
    const { app, repo, store } = setup();
    const res = await app.request(meetingsUrl, { headers: bearer("token-owner") });
    expect(res.status).toBe(200);
    expect(res.headers.get("cache-control")).toBe("no-store");
    expect(meetingsResponse.parse(await res.json()).items).toEqual([MEETING]);
    expect((store as FakeStore).meetingCalls[0]).toEqual({
      caller: { token: "token-owner", userId: OWNER },
      workspaceId: WS,
    });
    expect(repo.audit).toEqual([
      expect.objectContaining({
        workspaceId: WS,
        actorId: OWNER,
        action: "read.meeting_briefs",
        metadata: { count: 1 },
      }),
    ]);
  });

  it("refuses non-members without reading or auditing, and fails closed on audit errors", async () => {
    const { app, repo, store } = setup();
    for (const headers of [bearer("token-outsider"), {}, bearer("nope")]) {
      expect([401, 403]).toContain((await app.request(meetingsUrl, { headers })).status);
    }
    expect((store as FakeStore).meetingCalls).toHaveLength(0);
    expect(repo.audit).toHaveLength(0);
    repo.failAudit = true;
    const res = await app.request(meetingsUrl, { headers: bearer("token-owner") });
    expect(res.status).toBe(500);
    expect(await res.text()).not.toContain("Who signs off");
  });

  it("answers 503 reads_not_configured when no read store exists", async () => {
    const { app } = setup(null);
    expect((await app.request(meetingsUrl, { headers: bearer("token-owner") })).status).toBe(503);
  });
});

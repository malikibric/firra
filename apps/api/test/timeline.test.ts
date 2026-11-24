import { apiError, evidenceItem, evidenceResponse, timelineResponse } from "@firra/contracts";
import type { EvidenceItem, TimelineEvent } from "@firra/contracts";
import { describe, expect, it } from "vitest";
import { SupabaseReadStore } from "../src/lib/read-store.js";
import type { ReadCaller } from "../src/lib/read-store.js";
import { instant, page, parsePage } from "../src/lib/read-page.js";
import type { PageQuery } from "../src/lib/read-page.js";
import { WS, CLIENT, required } from "./client-fixtures.js";
import { USERS, bearer, testApp } from "./helpers.js";
export const EVIDENCE: EvidenceItem = {
  id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  client_id: CLIENT.id,
  quote: "Synthetic verified evidence quote",
  locator: { start: 0, end: 33 },
  deep_link: null,
  occurred_at: "2026-09-30T00:00:00.000Z",
  author_name: "Synthetic stakeholder",
  author_role: "CEO",
  subject: "Synthetic subject",
  source_ref: "bbbbbbbb",
};
const EVENT: TimelineEvent = {
  id: `outcome:${EVIDENCE.id}`,
  client_id: CLIENT.id,
  occurred_at: EVIDENCE.occurred_at,
  kind: "outcome",
  event: "renewed",
};
class Store extends SupabaseReadStore {
  missing = false;
  metadata = false;
  fail = false;
  calls = 0;
  constructor() {
    super("http://localhost:54321", "fixture");
  }
  override timeline(_c: ReadCaller, _w: string, _id: string, q: PageQuery) {
    this.calls++;
    if (this.fail) throw new Error("PRIVATE QUOTE");
    return Promise.resolve(this.missing ? null : page([EVENT], q));
  }
  override clientEvidence(_c: ReadCaller, _w: string, _id: string, q: PageQuery) {
    this.calls++;
    if (this.fail) throw new Error("PRIVATE QUOTE");
    return Promise.resolve(this.missing ? null : page(this.metadata ? [] : [EVIDENCE], q));
  }
  override evidence() {
    this.calls++;
    if (this.fail) throw new Error("PRIVATE QUOTE");
    return Promise.resolve(this.missing || this.metadata ? null : EVIDENCE);
  }
}
function setup() {
  const store = new Store();
  const t = testApp({
    readStore: store,
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
const root = `/v1/workspaces/${WS}`;
for (const [url, schema, action, targetType, targetId] of [
  [
    `${root}/clients/${CLIENT.id}/timeline`,
    timelineResponse,
    "read.client_timeline",
    "client",
    CLIENT.id,
  ],
  [
    `${root}/clients/${CLIENT.id}/evidence`,
    evidenceResponse,
    "read.client_evidence",
    "client",
    CLIENT.id,
  ],
  [`${root}/evidence/${EVIDENCE.id}`, evidenceItem, "read.evidence", "evidence", EVIDENCE.id],
] as const)
  describe(action, () => {
    it("validates success, audits once with ids/count only, and logs no rows", async () => {
      const { app, repo, logs } = setup();
      const res = await app.request(url, { headers: bearer("token-owner") });
      expect(res.status).toBe(200);
      schema.parse(await res.json());
      expect(repo.audit).toHaveLength(1);
      expect(repo.audit[0]).toMatchObject({ action, targetType, targetId, metadata: { count: 1 } });
      expect(res.headers.get("cache-control")).toBe("no-store");
      for (const secret of [EVIDENCE.quote, EVIDENCE.subject, EVIDENCE.author_name])
        expect(logs.join("\n")).not.toContain(secret);
    });
    it("allows admin and refuses contributors/outsiders/foreign workspaces before reads", async () => {
      const { app, repo, store } = setup();
      expect((await app.request(url, { headers: bearer("token-admin") })).status).toBe(200);
      for (const token of ["token-outsider", "token-contributor"])
        expect((await app.request(url, { headers: bearer(token) })).status).toBeGreaterThanOrEqual(
          401,
        );
      expect(
        (
          await app.request(url.replace(WS, crypto.randomUUID()), {
            headers: bearer("token-owner"),
          })
        ).status,
      ).toBe(403);
      expect(store.calls).toBe(1);
      expect(repo.audit).toHaveLength(1);
    });
    it("returns unaudited 404 for foreign, excluded or missing data", async () => {
      const { app, repo, store } = setup();
      store.missing = true;
      const res = await app.request(url, { headers: bearer("token-owner") });
      expect(res.status).toBe(404);
      apiError.parse(await res.json());
      expect(repo.audit).toEqual([]);
    });
    it("fails closed on store or audit failure without quote in errors or logs", async () => {
      for (const fail of ["store", "audit"]) {
        const { app, repo, store, logs } = setup();
        store.fail = fail === "store";
        repo.failAudit = fail === "audit";
        const res = await app.request(url, { headers: bearer("token-owner") });
        expect(res.status).toBe(500);
        apiError.parse(await res.json());
        expect(repo.audit).toEqual([]);
        expect(logs.join("\n")).not.toContain("PRIVATE QUOTE");
      }
    });
    it("rejects invalid path ids without audit", async () => {
      const { app, repo } = setup();
      expect(
        (await app.request(url.replace(targetId, "bad"), { headers: bearer("token-owner") }))
          .status,
      ).toBe(422);
      expect(repo.audit).toEqual([]);
    });
    if (targetType === "client")
      it("rejects malformed pagination before read or audit", async () => {
        const { app, repo, store } = setup();
        for (const query of [
          "limit=0",
          "limit=101",
          "limit=1.5",
          "cursor=bad",
          "cursor=",
          "cursor=" + Buffer.from("garbage|id").toString("base64url"),
        ]) {
          const res = await app.request(`${url}?${query}`, { headers: bearer("token-owner") });
          expect(res.status).toBe(422);
          apiError.parse(await res.json());
        }
        expect(store.calls).toBe(0);
        expect(repo.audit).toEqual([]);
      });
  });
describe("Cursor contract", () => {
  it("has stable descending ties, survives new inserts, and returns no duplicates", () => {
    const rows = ["aaaaaaaa", "bbbbbbbb", "cccccccc"].map((prefix) => ({
      ...EVIDENCE,
      id: `${prefix}-aaaa-4aaa-8aaa-aaaaaaaaaaaa`,
    }));
    const first = page(rows, required(parsePage("1", undefined, true)));
    const next = required(parsePage("2", required(first.next_cursor), true));
    const second = page([{ ...EVIDENCE, occurred_at: "2026-10-01T00:00:00Z" }, ...rows], next);
    expect(first.items.map((r) => r.id)).toEqual([required(rows[2]).id]);
    expect(second.items.map((r) => r.id)).toEqual([required(rows[1]).id, required(rows[0]).id]);
    expect(second.next_cursor).toBeNull();
    evidenceResponse.parse(first);
    evidenceResponse.parse(second);
  });
  it("rejects injection, invalid dates, ids, padded and wrong resource cursors", () => {
    for (const raw of [
      "2026-99-01T00:00:00Z|" + EVIDENCE.id,
      EVIDENCE.occurred_at + "|id.lt.foo",
      EVIDENCE.occurred_at + "|" + EVENT.id,
    ])
      expect(parsePage("50", Buffer.from(raw).toString("base64url"), true)).toBeNull();
    const encoded = Buffer.from(`${EVIDENCE.occurred_at}|${EVIDENCE.id}`).toString("base64url");
    expect(parsePage("50", encoded + "=", true)).toBeNull();
  });
});

describe("Metadata route responses", () => {
  it("returns timeline and empty evidence with audits, and unaudited 404 on single evidence", async () => {
    const { app, repo, store } = setup();
    store.metadata = true;
    const headers = bearer("token-owner");
    const timeline = await app.request(`${root}/clients/${CLIENT.id}/timeline`, { headers });
    expect(timeline.status).toBe(200);
    timelineResponse.parse(await timeline.json());
    const list = await app.request(`${root}/clients/${CLIENT.id}/evidence`, { headers });
    expect(list.status).toBe(200);
    expect(evidenceResponse.parse(await list.json())).toEqual({ items: [], next_cursor: null });
    expect((await app.request(`${root}/evidence/${EVIDENCE.id}`, { headers })).status).toBe(404);
    expect(repo.audit).toHaveLength(2);
    expect(repo.audit[1]).toMatchObject({ metadata: { count: 0 } });
  });
});

describe("Postgres timestamp precision", () => {
  it("preserves microseconds and normalizes offsets so adjacent source dates do not disappear", () => {
    expect(instant("2026-09-30T02:00:00.123456+02:00")).toBe("2026-09-30T00:00:00.123456Z");
    const rows = ["123455", "123456"].map((fraction) => ({
      ...EVIDENCE,
      occurred_at: `2026-09-30T00:00:00.${fraction}Z`,
    }));
    const first = page(rows, required(parsePage("1", undefined, true)));
    const second = page(rows, required(parsePage("1", required(first.next_cursor), true)));
    expect(second.items[0]?.occurred_at).toBe("2026-09-30T00:00:00.123455Z");
  });
});

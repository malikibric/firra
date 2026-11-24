import { randomUUID } from "node:crypto";
import { vaultAckResponse, vaultRecordsResponse } from "@firra/contracts";
import type { Database } from "@firra/db-types";
import { createClient } from "@supabase/supabase-js";
import { describe, expect, it } from "vitest";
import { parseVaultPage, SupabaseVaultStore } from "../src/lib/vault-store.js";
import { bearer, testApp, USERS } from "./helpers.js";
import { MemoryVaultStore, type OwnedVaultRow } from "./memory-vault-store.js";

const ws = randomUUID();
const userA = USERS["token-owner"]?.id ?? "";
const userB = USERS["token-outsider"]?.id ?? "";
function row(userId = userA, workspaceId = ws): OwnedVaultRow {
  return {
    id: randomUUID(),
    workspace_id: workspaceId,
    provider: "gmail",
    external_id: randomUUID(),
    kind: "email",
    client_id: randomUUID(),
    thread_key: null,
    occurred_at: "2026-10-01T12:00:00Z",
    direction: "in",
    subject: "synthetic subject",
    deep_link: null,
    content_hash: "\\x0102",
    word_count: 2,
    body: "synthetic secret mail",
    created_at: "2026-10-01T12:00:00.123456Z",
    vault_user_id: userId,
    vault_delivered_at: null,
    policy: "full",
  };
}
function setup() {
  const store = new MemoryVaultStore();
  store.scopes.set(userA, [ws]);
  store.scopes.set(userB, [ws]);
  return { store, ...testApp({ vaultStore: store }) };
}
function ack(app: ReturnType<typeof testApp>["app"], ids: string[], token = "token-owner") {
  return app.request("/v1/vault/ack", {
    method: "POST",
    headers: { ...bearer(token), "content-type": "application/json" },
    body: JSON.stringify({ ids }),
  });
}

describe("vault routes", () => {
  it("isolates two users in the same workspace, including a contributor without membership", async () => {
    const { store, app, repo, logs } = setup();
    const own = row();
    const contributor = row(userB);
    store.rows.push(
      own,
      contributor,
      { ...row(), policy: "metadata_only" },
      { ...row(), policy: "excluded" },
    );
    repo.addMember(ws, userA, "owner");
    for (const [token, expected] of [
      ["token-owner", own.id],
      ["token-outsider", contributor.id],
    ] as const) {
      const response = await app.request("/v1/vault/records", { headers: bearer(token) });
      expect(response.status).toBe(200);
      expect(response.headers.get("cache-control")).toBe("no-store");
      const body = vaultRecordsResponse.parse(await response.json());
      expect(body.items.map((item) => item.id)).toEqual([expected]);
      expect(body.items[0]).not.toHaveProperty("vault_user_id");
      expect(body.items[0]).not.toHaveProperty("created_at");
      expect(body.items[0]).not.toHaveProperty("raw_object_uri");
    }
    expect(repo.audit.map((entry) => [entry.action, entry.metadata.count])).toEqual([
      ["read.vault_records", 1],
      ["read.vault_records", 1],
    ]);
    expect(logs.join("\n")).not.toContain(own.body);
    expect(logs.join("\n")).not.toContain(own.subject);
  });
  it("acks only owned ids, deduplicates, audits empty polls, and delivers changed content again", async () => {
    const { store, app, repo } = setup();
    const own = row();
    const foreign = row(userB);
    store.rows.push(own, foreign);
    expect(
      vaultAckResponse.parse(await (await ack(app, [foreign.id, randomUUID()])).json()),
    ).toEqual({ acknowledged: 0 });
    expect(foreign.vault_delivered_at).toBeNull();
    expect(
      vaultAckResponse.parse(await (await ack(app, [own.id, own.id, foreign.id])).json()),
    ).toEqual({ acknowledged: 1 });
    expect(vaultAckResponse.parse(await (await ack(app, [own.id])).json())).toEqual({
      acknowledged: 0,
    });
    const empty = await app.request("/v1/vault/records", { headers: bearer("token-owner") });
    expect(await empty.json()).toEqual({ items: [], next_cursor: null });
    expect(repo.audit.at(-1)).toMatchObject({
      action: "read.vault_records",
      metadata: { count: 0 },
    });
    // T67's upsert resets delivery on a changed content hash.
    own.content_hash = "\\x0203";
    own.body = "changed synthetic mail";
    own.vault_delivered_at = null;
    const again = vaultRecordsResponse.parse(
      await (
        await app.request("/v1/vault/records", {
          headers: bearer("token-owner"),
        })
      ).json(),
    );
    expect(again.items[0]?.body).toBe(own.body);
  });
  it("audits every returned workspace and every empty workspace without exposing foreign scopes", async () => {
    const { store, app, repo } = setup();
    const otherWs = randomUUID();
    store.scopes.set(userA, [ws, otherWs]);
    store.rows.push(row(), row(userA, otherWs), row(userB, randomUUID()));
    await app.request("/v1/vault/records", { headers: bearer("token-owner") });
    expect(repo.audit.map((entry) => entry.workspaceId).sort()).toEqual([ws, otherWs].sort());
    store.rows = [];
    repo.audit = [];
    await app.request("/v1/vault/records", { headers: bearer("token-owner") });
    expect(repo.audit).toHaveLength(2);
    expect(repo.audit.every((entry) => entry.metadata.count === 0)).toBe(true);
  });
  it("fails closed on read/empty audit failure and does not acknowledge after audit failure", async () => {
    const { store, app, repo, logs } = setup();
    const own = row();
    store.rows.push(own);
    repo.failAudit = true;
    for (const response of [
      await app.request("/v1/vault/records", { headers: bearer("token-owner") }),
      await ack(app, [own.id]),
    ]) {
      expect(response.status).toBe(500);
      expect(await response.text()).not.toContain(own.body);
    }
    expect(store.ackCalls).toBe(0);
    expect(own.vault_delivered_at).toBeNull();
    store.rows = [];
    expect(
      (await app.request("/v1/vault/records", { headers: bearer("token-owner") })).status,
    ).toBe(500);
    expect(logs.join("\n")).not.toContain(own.body);
  });
  it("fails closed when an empty poll has no workspace for its mandatory audit", async () => {
    const { store, app } = setup();
    store.scopes.clear();
    expect(
      (await app.request("/v1/vault/records", { headers: bearer("token-owner") })).status,
    ).toBe(500);
  });
  it("paginates tied timestamps with microsecond precision without duplicates", async () => {
    const { store, app } = setup();
    store.rows.push(row(), row(), { ...row(), created_at: "2026-10-01T12:00:00.123455Z" });
    const first = vaultRecordsResponse.parse(
      await (
        await app.request("/v1/vault/records?limit=1", {
          headers: bearer("token-owner"),
        })
      ).json(),
    );
    const second = vaultRecordsResponse.parse(
      await (
        await app.request(`/v1/vault/records?limit=2&cursor=${first.next_cursor}`, {
          headers: bearer("token-owner"),
        })
      ).json(),
    );
    expect(new Set([...first.items, ...second.items].map((item) => item.id)).size).toBe(3);
    expect(second.next_cursor).toBeNull();
  });
  it.each([
    "limit=0",
    "limit=101",
    "limit=1.5",
    "limit=-1",
    "limit=1e2",
    "cursor=",
    "cursor=bad!",
    `cursor=${Buffer.from("2026-10-01T00:00:00Z|bad").toString("base64url")}`,
    `cursor=${Buffer.from(`2026-10-01T00:00:00Z|${randomUUID()}|extra`).toString("base64url")}`,
    `cursor=${Buffer.from(`2026-10-01T00:00:00.1234567Z|${randomUUID()}`).toString("base64url")}`,
  ])("rejects invalid query %s before the store reads", async (query) => {
    const { app, store } = setup();
    expect(
      (await app.request(`/v1/vault/records?${query}`, { headers: bearer("token-owner") })).status,
    ).toBe(422);
    expect(store.readCalls).toBe(0);
  });
  it.each([
    { ids: [] },
    { ids: ["bad"] },
    { ids: Array.from({ length: 201 }, () => randomUUID()) },
  ])("rejects invalid ack ids", async ({ ids }) => {
    const { app } = setup();
    expect((await ack(app, ids)).status).toBe(422);
  });
  it("requires JSON, user auth and configured store; applies per-user limits", async () => {
    const { app } = setup();
    expect((await app.request("/v1/vault/records")).status).toBe(401);
    expect(
      (
        await app.request("/v1/vault/ack", {
          method: "POST",
          headers: bearer("token-owner"),
          body: "{}",
        })
      ).status,
    ).toBe(422);
    const unconfigured = testApp().app;
    expect(
      (await unconfigured.request("/v1/vault/records", { headers: bearer("token-owner") })).status,
    ).toBe(503);
    expect((await ack(unconfigured, [randomUUID()])).status).toBe(503);
    const limited = testApp({ vaultStore: new MemoryVaultStore(), userRateLimitPerMinute: 1 }).app;
    await limited.request("/v1/vault/records", { headers: bearer("token-owner") });
    expect((await ack(limited, [randomUUID()])).status).toBe(429);
    expect(
      (await limited.request("/v1/vault/records", { headers: bearer("token-outsider") })).status,
    ).not.toBe(429);
  });
  it("delivers nothing to a user who is no longer a member or active contributor", async () => {
    const recordCalls: URL[] = [];
    const db = createClient<Database>("http://127.0.0.1:54321", "synthetic-key", {
      auth: { persistSession: false },
      global: {
        fetch: (url) => {
          const parsed = new URL(url instanceof Request ? url.url : url.toString());
          if (parsed.pathname.endsWith("/source_record")) recordCalls.push(parsed);
          return Promise.resolve(
            new Response(JSON.stringify([]), {
              status: 200,
              headers: { "content-type": "application/json" },
            }),
          );
        },
      },
    });
    const store = new SupabaseVaultStore(db);
    expect(await store.records(userA, { limit: 50, cursor: null })).toEqual([]);
    expect(await store.pendingAck(userA, [randomUUID()])).toEqual([]);
    expect(await store.ack(userA, [randomUUID()])).toBe(0);
    expect(await store.ownMailboxes(userA)).toEqual([]);
    expect(recordCalls).toHaveLength(0);
  });
});

const CONNECTION = randomUUID();
/** Answers the scope lookups (membership, contributor, connection) and hands record calls to `records`. */
function routed(records: (url: URL, init?: RequestInit) => Response) {
  return (url: string | URL | Request, init?: RequestInit): Promise<Response> => {
    const parsed = new URL(url instanceof Request ? url.url : url.toString());
    const json = (body: unknown) =>
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    if (parsed.pathname.endsWith("/membership"))
      return Promise.resolve(json([{ workspace_id: ws }]));
    if (parsed.pathname.endsWith("/contributor")) return Promise.resolve(json([]));
    if (parsed.pathname.endsWith("/connection"))
      return Promise.resolve(json([{ id: CONNECTION, workspace_id: ws }]));
    return Promise.resolve(records(parsed, init));
  };
}

describe("service-role vault store", () => {
  it("filters ownership in the body query, applies policy and cursor, and trims subjects", async () => {
    const requests: URL[] = [];
    const fixture = row();
    const db = createClient<Database>("http://127.0.0.1:54321", "synthetic-key", {
      auth: { persistSession: false },
      global: {
        fetch: routed((url) => {
          requests.push(url);
          return new Response(
            JSON.stringify([
              {
                ...fixture,
                body_clean: fixture.body,
                metadata: { subject: `  ${"s".repeat(310)}  ` },
              },
            ]),
            { status: 200, headers: { "content-type": "application/json" } },
          );
        }),
      },
    });
    const store = new SupabaseVaultStore(db);
    const cursor = Buffer.from(`${fixture.created_at}|${fixture.id}`).toString("base64url");
    const query = parseVaultPage("100", cursor);
    expect(query).not.toBeNull();
    if (!query) throw new Error("invalid fixture cursor");
    const rows = await store.records(userA, query);
    expect(rows[0]?.subject).toHaveLength(300);
    const params = requests[0]?.searchParams;
    expect(params?.get("vault_user_id")).toBe(`eq.${userA}`);
    expect(params?.get("connection_id")).toBe(`in.(${CONNECTION})`);
    expect(params?.get("body_clean")).toBe("not.is.null");
    expect(params?.get("vault_delivered_at")).toBe("is.null");
    expect(params?.get("client.policy")).toBe("eq.full");
    expect(params?.get("limit")).toBe("101");
    expect(params?.get("order")).toBe("created_at.desc,id.desc");
    expect(params?.get("or")).toContain(fixture.id);
    expect(params?.get("select")).not.toContain("raw_object_uri");
  });
  it("keeps provider error text out of exceptions and does not issue an empty ack mutation", async () => {
    const secret = "synthetic provider body in an error";
    const requests: string[] = [];
    const db = createClient<Database>("http://127.0.0.1:54321", "synthetic-key", {
      auth: { persistSession: false },
      global: {
        fetch: routed((url) => {
          requests.push(url.toString());
          return new Response(JSON.stringify({ code: "fixture", message: secret }), {
            status: 500,
            headers: { "content-type": "application/json" },
          });
        }),
      },
    });
    const store = new SupabaseVaultStore(db);
    await expect(store.records(userA, { limit: 50, cursor: null })).rejects.toThrow(
      "vault read failed",
    );
    await expect(store.pendingAck(userA, [randomUUID()])).rejects.toThrow(
      "vault ack lookup failed",
    );
    await expect(store.ack(userA, [randomUUID()])).rejects.toThrow("vault ack failed");
    const before = requests.length;
    expect(await store.ack(userA, [])).toBe(0);
    expect(requests).toHaveLength(before);
  });
  it("finds empty-read audit scopes from only the caller's memberships, active links and owned records", async () => {
    const requests: URL[] = [];
    const db = createClient<Database>("http://127.0.0.1:54321", "synthetic-key", {
      auth: { persistSession: false },
      global: {
        fetch: (url) => {
          requests.push(new URL(url instanceof Request ? url.url : url.toString()));
          return Promise.resolve(
            new Response(JSON.stringify([{ workspace_id: ws }]), {
              status: 200,
              headers: { "content-type": "application/json" },
            }),
          );
        },
      },
    });
    expect(await new SupabaseVaultStore(db).auditWorkspaces(userB)).toEqual([ws]);
    expect(requests).toHaveLength(3);
    for (const request of requests) {
      const key = request.pathname.endsWith("source_record") ? "vault_user_id" : "user_id";
      expect(request.searchParams.get(key)).toBe(`eq.${userB}`);
      expect(request.searchParams.get("select")).toBe("workspace_id");
      if (request.pathname.endsWith("contributor"))
        expect(request.searchParams.get("status")).toBe("eq.active");
    }
  });
  it("filters both ack metadata and mutation by caller, never reads bodies, masks store errors", async () => {
    const requests: { url: URL; method: string }[] = [];
    const db = createClient<Database>("http://127.0.0.1:54321", "synthetic-key", {
      auth: { persistSession: false },
      global: {
        fetch: routed((url, init) => {
          requests.push({ url, method: init?.method ?? "GET" });
          return new Response(JSON.stringify([{ id: randomUUID(), workspace_id: ws }]), {
            status: 200,
            headers: { "content-type": "application/json" },
          });
        }),
      },
    });
    const store = new SupabaseVaultStore(db);
    const ids = [randomUUID()];
    await store.pendingAck(userB, ids);
    expect(await store.ack(userB, ids)).toBe(1);
    expect(requests.map((request) => request.method)).toEqual(["GET", "PATCH"]);
    for (const request of requests) {
      expect(request.url.searchParams.get("vault_user_id")).toBe(`eq.${userB}`);
      expect(request.url.searchParams.get("connection_id")).toBe(`in.(${CONNECTION})`);
      expect(request.url.searchParams.get("select")).not.toContain("body_clean");
    }
  });
  it("delivers nothing to a user who is no longer a member or active contributor", async () => {
    const recordCalls: URL[] = [];
    const db = createClient<Database>("http://127.0.0.1:54321", "synthetic-key", {
      auth: { persistSession: false },
      global: {
        fetch: (url) => {
          const parsed = new URL(url instanceof Request ? url.url : url.toString());
          if (parsed.pathname.endsWith("/source_record")) recordCalls.push(parsed);
          return Promise.resolve(
            new Response(JSON.stringify([]), {
              status: 200,
              headers: { "content-type": "application/json" },
            }),
          );
        },
      },
    });
    const store = new SupabaseVaultStore(db);
    expect(await store.records(userA, { limit: 50, cursor: null })).toEqual([]);
    expect(await store.pendingAck(userA, [randomUUID()])).toEqual([]);
    expect(await store.ack(userA, [randomUUID()])).toBe(0);
    expect(await store.ownMailboxes(userA)).toEqual([]);
    expect(recordCalls).toHaveLength(0);
  });
});

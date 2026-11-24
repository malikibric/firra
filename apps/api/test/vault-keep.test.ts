import { randomUUID } from "node:crypto";
import { vaultKeepResponse, openApiDocument } from "@firra/contracts";
import type { Database } from "@firra/db-types";
import { createClient } from "@supabase/supabase-js";
import { expect, it } from "vitest";
import { SupabaseVaultStore } from "../src/lib/vault-store.js";
import { bearer, testApp, USERS } from "./helpers.js";
import { MemoryVaultStore } from "./memory-vault-store.js";

const user = USERS["token-owner"]?.id ?? "";
const contributor = USERS["token-outsider"]?.id ?? "";
function setup(rate = 120) {
  const store = new MemoryVaultStore();
  const ws = randomUUID();
  const other = randomUUID();
  store.scopes.set(user, [ws, other]);
  store.contributors.push({ user_id: contributor, workspace_id: ws, status: "active" });
  const full = randomUUID();
  store.clients.push(
    { id: full, workspace_id: ws, policy: "full" },
    { id: randomUUID(), workspace_id: ws, policy: "metadata_only" },
    { id: randomUUID(), workspace_id: ws, policy: "excluded" },
    { id: randomUUID(), workspace_id: randomUUID(), policy: "full" },
  );
  return {
    store,
    ws,
    other,
    full,
    ...testApp({ vaultStore: store, userRateLimitPerMinute: rate }),
  };
}
function keep(app: ReturnType<typeof testApp>["app"], body: unknown, token = "token-owner") {
  return app.request("/v1/vault/keep", {
    method: "POST",
    headers: { ...bearer(token), "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}
it("keeps only requested own full clients, deduplicates, audits every caller workspace without caching", async () => {
  const { app, store, repo, ws, other, full } = setup();
  const response = await keep(app, {
    client_ids: [...store.clients.map((row) => row.id), full, randomUUID()],
  });
  expect(response.status).toBe(200);
  expect(response.headers.get("cache-control")).toBe("no-store");
  expect(vaultKeepResponse.parse(await response.json())).toEqual({ keep: [full] });
  expect(
    repo.audit.map(({ workspaceId, actorId, action, metadata }) => ({
      workspaceId,
      actorId,
      action,
      metadata,
    })),
  ).toEqual(
    [ws, other].map((workspaceId) => ({
      workspaceId,
      actorId: user,
      action: "read.vault_keep",
      metadata: { count: 1 },
    })),
  );
  expect(await (await keep(app, { client_ids: [randomUUID()] })).json()).toEqual({ keep: [] });
  expect(repo.audit.slice(-2).map((row) => row.metadata)).toEqual([{ count: 0 }, { count: 0 }]);
});
it("allows active contributors without membership, and keeps nothing after revocation", async () => {
  const { app, store, full } = setup();
  expect(await (await keep(app, { client_ids: [full] }, "token-outsider")).json()).toEqual({
    keep: [full],
  });
  const link = store.contributors[0];
  if (!link) throw new Error("missing contributor fixture");
  link.status = "revoked";
  // A second active workspace lets a revoked contributor receive an audited empty answer.
  store.contributors.push({ user_id: contributor, workspace_id: randomUUID(), status: "active" });
  expect(await (await keep(app, { client_ids: [full] }, "token-outsider")).json()).toEqual({
    keep: [],
  });
});
it("fails closed for missing workspace, unavailable store and failed audit", async () => {
  const { app, repo, full, store } = setup();
  repo.failAudit = true;
  const failed = await keep(app, { client_ids: [full] });
  expect(failed.status).toBe(500);
  expect(await failed.json()).not.toHaveProperty("keep");
  repo.failAudit = false;
  store.scopes.clear();
  expect((await keep(app, { client_ids: [full] })).status).toBe(500);
  expect((await keep(testApp({ vaultStore: null }).app, { client_ids: [full] })).status).toBe(503);
});
it("rejects invalid bodies and unauthenticated callers, and applies the per-user limit", async () => {
  const { app, repo } = setup();
  for (const body of [
    null,
    {},
    { client_ids: [] },
    { client_ids: ["bad"] },
    { client_ids: Array.from({ length: 201 }, () => randomUUID()) },
    { client_ids: [randomUUID()], extra: true },
  ]) {
    expect((await keep(app, body)).status).toBe(422);
  }
  expect(repo.audit).toHaveLength(0);
  expect((await keep(app, { client_ids: [randomUUID()] }, "bad-token")).status).toBe(401);
  const limited = setup(1).app;
  expect((await keep(limited, { client_ids: [randomUUID()] })).status).toBe(200);
  expect((await keep(limited, { client_ids: [randomUUID()] })).status).toBe(429);
});
it("registers the strict request and response in OpenAPI", () => {
  const doc = openApiDocument("fixture") as {
    paths: Record<string, unknown>;
    components: { schemas: Record<string, unknown> };
  };
  expect(doc.paths["/vault/keep"]).toMatchObject({ post: { operationId: "keepVaultClients" } });
  expect(doc.components.schemas.VaultKeepRequest).toMatchObject({
    additionalProperties: false,
    properties: { client_ids: { minItems: 1, maxItems: 200 } },
  });
  expect(doc.components.schemas.VaultKeepResponse).toMatchObject({
    properties: { keep: { maxItems: 200 } },
  });
});
it("service-role queries scope both links and clients explicitly, without reading client content", async () => {
  const requests: URL[] = [];
  const ws = randomUUID(),
    contributorWs = randomUUID(),
    id = randomUUID();
  const db = createClient<Database>("http://127.0.0.1:54321", "synthetic-key", {
    auth: { persistSession: false },
    global: {
      fetch: (url) => {
        const request = new URL(url instanceof Request ? url.url : url.toString());
        requests.push(request);
        const data = request.pathname.endsWith("client")
          ? [{ id }]
          : [{ workspace_id: request.pathname.endsWith("membership") ? ws : contributorWs }];
        return Promise.resolve(
          new Response(JSON.stringify(data), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      },
    },
  });
  expect(await new SupabaseVaultStore(db).keepable(user, [id, id])).toEqual([id]);
  expect(requests).toHaveLength(3);
  for (const request of requests.slice(0, 2)) {
    expect(request.searchParams.get("user_id")).toBe(`eq.${user}`);
    expect(request.searchParams.get("select")).toBe("workspace_id");
  }
  expect(requests[1]?.searchParams.get("status")).toBe("eq.active");
  const params = requests[2]?.searchParams;
  if (!params) throw new Error("missing client query");
  expect(params.get("select")).toBe("id");
  expect(params.get("policy")).toBe("eq.full");
  expect(params.get("workspace_id")).toBe(`in.(${ws},${contributorWs})`);
  expect(params.get("id")).toBe(`in.(${id})`);
});
it("does not keep deleted clients or clients whose policy changed, and accepts 200 duplicate ids", async () => {
  const { app, store, full } = setup();
  expect(await (await keep(app, { client_ids: Array<string>(200).fill(full) })).json()).toEqual({
    keep: [full],
  });
  const client = store.clients.find((row) => row.id === full);
  if (!client) throw new Error("missing client fixture");
  client.policy = "metadata_only";
  expect(await (await keep(app, { client_ids: [full] })).json()).toEqual({ keep: [] });
  store.clients = [];
  expect(await (await keep(app, { client_ids: [full] })).json()).toEqual({ keep: [] });
});
it("skips client queries without active scopes and masks errors at both lookup stages", async () => {
  for (const failingTable of [null, "membership", "contributor", "client"]) {
    const requests: URL[] = [];
    const db = createClient<Database>("http://127.0.0.1:54321", "synthetic-key", {
      auth: { persistSession: false },
      global: {
        fetch: (url) => {
          const request = new URL(url instanceof Request ? url.url : url.toString());
          requests.push(request);
          const fail = failingTable !== null && request.pathname.endsWith(failingTable);
          const data = fail
            ? { message: "synthetic sensitive error" }
            : failingTable
              ? [{ workspace_id: randomUUID() }]
              : [];
          return Promise.resolve(
            new Response(JSON.stringify(data), {
              status: fail ? 500 : 200,
              headers: { "content-type": "application/json" },
            }),
          );
        },
      },
    });
    const result = new SupabaseVaultStore(db).keepable(user, [randomUUID()]);
    if (failingTable)
      await expect(result).rejects.toThrow(
        failingTable === "client" ? "vault keep lookup failed" : "vault keep scope lookup failed",
      );
    else {
      expect(await result).toEqual([]);
      expect(requests).toHaveLength(2);
    }
  }
});

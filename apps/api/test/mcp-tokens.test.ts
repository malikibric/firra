import { mcpTokenCreated, mcpTokensResponse } from "@firra/contracts";
import { describe, expect, it } from "vitest";
import type { CreateMcpTokenRequest, McpTokenCreated, McpTokenItem } from "@firra/contracts";
import type { ReadCaller } from "../src/lib/read-store.js";
import { TokenForbiddenError, TooManyTokensError, type McpTokenAdmin } from "../src/mcp/admin.js";
import { USERS, bearer, testApp } from "./helpers.js";

const WS = "11111111-1111-4111-8111-111111111111";
const OWNER = USERS["token-owner"]?.id ?? "";
const SECRET = `frm_${"ab".repeat(32)}`;
const ITEM: McpTokenItem = {
  id: "33333333-3333-4333-8333-333333333333",
  user_id: OWNER,
  name: "laptop",
  token_prefix: "frm_abababab",
  scopes: ["read"],
  expires_at: "2027-01-01T00:00:00+00:00",
  last_used_at: null,
  revoked_at: null,
  created_at: "2026-10-02T09:00:00+00:00",
};

class FakeAdmin implements McpTokenAdmin {
  calls: string[] = [];
  mode: "ok" | "forbidden" | "too_many" | "boom" = "ok";
  create(caller: ReadCaller, workspaceId: string, request: CreateMcpTokenRequest) {
    this.calls.push(`create ${caller.userId} ${workspaceId} ${request.name} ${request.days}`);
    if (this.mode === "forbidden") return Promise.reject(new TokenForbiddenError());
    if (this.mode === "too_many") return Promise.reject(new TooManyTokensError());
    if (this.mode === "boom") return Promise.reject(new Error("PRIVATE"));
    const created: McpTokenCreated = {
      id: ITEM.id,
      token: SECRET,
      token_prefix: "frm_abababab",
      expires_at: ITEM.expires_at,
    };
    return Promise.resolve(created);
  }
  list(_caller: ReadCaller, workspaceId: string) {
    this.calls.push(`list ${workspaceId}`);
    return Promise.resolve([ITEM]);
  }
  revoke(_caller: ReadCaller, workspaceId: string, tokenId: string) {
    this.calls.push(`revoke ${workspaceId} ${tokenId}`);
    if (this.mode === "forbidden") return Promise.reject(new TokenForbiddenError());
    return Promise.resolve();
  }
}

function setup(admin: McpTokenAdmin | null = new FakeAdmin()) {
  const t = testApp({ mcpTokens: admin });
  t.repo.addMember(WS, OWNER, "owner");
  return { ...t, admin };
}
const base = `/v1/workspaces/${WS}/mcp/tokens`;
const json = (token: string, body: unknown) => ({
  method: "POST",
  headers: { ...bearer(token), "content-type": "application/json" },
  body: JSON.stringify(body),
});

describe("MCP token routes", () => {
  it("creates a token as the caller, returns the secret once, never cached", async () => {
    const { app, admin } = setup();
    const res = await app.request(base, json("token-owner", { name: "  laptop  " }));
    expect(res.status).toBe(201);
    expect(res.headers.get("cache-control")).toBe("no-store");
    expect(mcpTokenCreated.parse(await res.json()).token).toBe(SECRET);
    expect((admin as FakeAdmin).calls).toEqual([`create ${OWNER} ${WS} laptop 90`]);
  });

  it("validates the body, refuses outsiders and maps RPC refusals without leaks", async () => {
    const { app, admin } = setup();
    expect((await app.request(base, json("token-owner", { name: "" }))).status).toBe(422);
    expect((await app.request(base, json("token-owner", { name: "x", days: 1000 }))).status).toBe(
      422,
    );
    expect((await app.request(base, json("token-outsider", { name: "x" }))).status).toBe(403);
    expect((await app.request(base, { method: "POST" })).status).toBe(401);
    (admin as FakeAdmin).mode = "forbidden";
    expect((await app.request(base, json("token-owner", { name: "x" }))).status).toBe(404);
    (admin as FakeAdmin).mode = "too_many";
    const many = await app.request(base, json("token-owner", { name: "x" }));
    expect(many.status).toBe(422);
    (admin as FakeAdmin).mode = "boom";
    const boom = await app.request(base, json("token-owner", { name: "x" }));
    expect(boom.status).toBe(500);
    expect(await boom.text()).not.toContain("PRIVATE");
  });

  it("lists token metadata (no secret field) and revokes", async () => {
    const { app, admin } = setup();
    const list = await app.request(base, { headers: bearer("token-owner") });
    expect(list.status).toBe(200);
    const body = mcpTokensResponse.parse(await list.json());
    expect(body.items).toEqual([ITEM]);
    expect(JSON.stringify(body)).not.toContain("token_hash");
    const del = await app.request(`${base}/${ITEM.id}`, {
      method: "DELETE",
      headers: bearer("token-owner"),
    });
    expect(del.status).toBe(204);
    expect((admin as FakeAdmin).calls).toContain(`revoke ${WS} ${ITEM.id}`);
    (admin as FakeAdmin).mode = "forbidden";
    const gone = await app.request(`${base}/${ITEM.id}`, {
      method: "DELETE",
      headers: bearer("token-owner"),
    });
    expect(gone.status).toBe(404);
  });

  it("answers 503 without a configured store and 422 for a malformed token id", async () => {
    const { app } = setup(null);
    expect((await app.request(base, { headers: bearer("token-owner") })).status).toBe(503);
    const t = setup();
    const bad = await t.app.request(`${base}/not-a-uuid`, {
      method: "DELETE",
      headers: bearer("token-owner"),
    });
    expect(bad.status).toBe(422);
  });
});

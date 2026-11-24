import { describe, expect, it } from "vitest";
import type { AttentionItem } from "@firra/contracts";
import type { ReadCaller, ReadStore } from "../src/lib/read-store.js";
import type { McpTokenRecord, McpTokenStore, SessionMinter } from "../src/mcp/tokens.js";
import type { AppDeps } from "../src/app.js";
import { USERS, testApp } from "./helpers.js";

const WS = "11111111-1111-4111-8111-111111111111";
const OWNER = USERS["token-owner"]?.id ?? "";
const SECRET = `frm_${"ab".repeat(32)}`;
const TOKEN: McpTokenRecord = {
  id: "33333333-3333-4333-8333-333333333333",
  workspaceId: WS,
  userId: OWNER,
  scopes: ["read"],
};
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
  drivers: [],
};

class FakeTokens implements McpTokenStore {
  revoked = false;
  resolve(secret: string): Promise<McpTokenRecord | null> {
    return Promise.resolve(secret === SECRET && !this.revoked ? TOKEN : null);
  }
}
class FakeMinter implements SessionMinter {
  minted: string[] = [];
  mint(userId: string): Promise<string> {
    this.minted.push(userId);
    return Promise.resolve("minted-session-jwt");
  }
}
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
  clients(): ReturnType<ReadStore["clients"]> {
    return Promise.resolve([]);
  }
  clientDetail(): ReturnType<ReadStore["clientDetail"]> {
    return Promise.resolve(null);
  }

  calls: { caller: ReadCaller; workspaceId: string }[] = [];
  fail = false;
  upcomingMeetings(): ReturnType<ReadStore["upcomingMeetings"]> {
    return Promise.resolve([]);
  }
  attention(caller: ReadCaller, workspaceId: string): Promise<AttentionItem[]> {
    this.calls.push({ caller, workspaceId });
    if (this.fail) return Promise.reject(new Error("PRIVATE ROW DATA"));
    return Promise.resolve([ITEM]);
  }
}

function setup(extra: Partial<AppDeps> = {}) {
  const store = new FakeStore();
  const tokens = new FakeTokens();
  const minter = new FakeMinter();
  const errors: unknown[] = [];
  const t = testApp({
    readStore: store,
    mcpTokenStore: tokens,
    sessionMinter: minter,
    captureException: (e: unknown) => errors.push(e),
    ...extra,
  });
  t.repo.addMember(WS, OWNER, "owner");
  return { ...t, store, tokens, minter, errors };
}

const HEADERS = {
  "content-type": "application/json",
  accept: "application/json, text/event-stream",
  authorization: `Bearer ${SECRET}`,
};
const call = {
  jsonrpc: "2.0",
  id: 1,
  method: "tools/call",
  params: { name: "get_clients_needing_attention", arguments: {} },
};
const post = (body: unknown, headers: Record<string, string> = {}) => ({
  method: "POST",
  headers: { ...HEADERS, "mcp-protocol-version": "2025-11-25", ...headers },
  body: JSON.stringify(body),
});

async function payload(res: Response): Promise<{ result?: Record<string, unknown> }> {
  const text = await res.text();
  // Streamable HTTP may answer with a JSON body or one SSE event.
  const data =
    text.startsWith("event:") || text.startsWith("data:")
      ? (text.split("\n").find((l) => l.startsWith("data:")) ?? "").slice(5)
      : text;
  return JSON.parse(data) as { result?: Record<string, unknown> };
}

describe("POST /mcp", () => {
  it("serves a 2025-era client: tool call runs as the token's user, audited as an mcp actor", async () => {
    const { app, store, minter, repo } = setup();
    const res = await app.request("/mcp", post(call, { "user-agent": "Claude/1" }));
    expect(res.status).toBe(200);
    const body = await payload(res);
    const structured = body.result?.structuredContent as { items: AttentionItem[] };
    expect(structured.items[0]?.name).toBe("Redfern");
    expect(JSON.stringify(body.result?.content)).toContain("Redfern: high since 2026-09-25");
    expect(store.calls).toEqual([
      { caller: { token: "minted-session-jwt", userId: OWNER }, workspaceId: WS },
    ]);
    expect(minter.minted).toEqual([OWNER]);
    expect(repo.audit).toEqual([
      expect.objectContaining({
        workspaceId: WS,
        actorType: "mcp",
        actorId: TOKEN.id,
        action: "mcp.get_clients_needing_attention",
        userAgent: "Claude/1",
        metadata: { count: 1 },
      }),
    ]);
  });

  it("serves a 2026-07-28 client with the per-request envelope headers", async () => {
    const { app } = setup();
    const modern = {
      ...call,
      params: {
        ...call.params,
        _meta: {
          "io.modelcontextprotocol/protocolVersion": "2026-07-28",
          "io.modelcontextprotocol/clientInfo": { name: "t", version: "1" },
          "io.modelcontextprotocol/clientCapabilities": {},
        },
      },
    };
    const res = await app.request(
      "/mcp",
      post(modern, {
        "mcp-protocol-version": "2026-07-28",
        "mcp-method": "tools/call",
        "mcp-name": "get_clients_needing_attention",
      }),
    );
    expect(res.status).toBe(200);
    expect(JSON.stringify((await payload(res)).result)).toContain("Redfern");
  });

  it("refuses without a valid token, with a revoked token, and after the membership ended", async () => {
    const { app, tokens, repo, store } = setup();
    const none = await app.request("/mcp", post(call, { authorization: "" }));
    expect(none.status).toBe(401);
    expect(none.headers.get("www-authenticate")).toContain("Bearer");
    expect(
      (await app.request("/mcp", post(call, { authorization: `Bearer frm_${"cd".repeat(32)}` })))
        .status,
    ).toBe(401);
    tokens.revoked = true;
    expect((await app.request("/mcp", post(call))).status).toBe(401);
    tokens.revoked = false;
    repo.memberships.clear();
    expect((await app.request("/mcp", post(call))).status).toBe(401);
    expect(store.calls).toHaveLength(0);
    expect(repo.audit).toHaveLength(0);
  });

  it("denies by default: a token without the read scope runs no tool", async () => {
    const { app, store, repo } = setup({
      mcpTokenStore: { resolve: () => Promise.resolve({ ...TOKEN, scopes: [] }) },
    });
    const res = await app.request("/mcp", post(call));
    const text = JSON.stringify(await payload(res));
    expect(text).toContain("Forbidden.");
    expect(store.calls).toHaveLength(0);
    expect(repo.audit).toHaveLength(0);
  });

  it("denies every browser origin when WEB_URL and the explicit allowlist are absent", async () => {
    const { app, store, repo } = setup({ webUrl: undefined, mcpAllowedOrigins: [] });
    expect((await app.request("/mcp", post(call, { origin: "https://app.example" }))).status).toBe(
      403,
    );
    expect(store.calls).toHaveLength(0);
    expect(repo.audit).toHaveLength(0);
  });

  it("validates Origin, allows only POST, limits the body and rejects bad JSON", async () => {
    const { app } = setup({ mcpAllowedOrigins: ["https://claude.ai"] });
    expect((await app.request("/mcp", post(call, { origin: "https://evil.example" }))).status).toBe(
      403,
    );
    expect((await app.request("/mcp", post(call, { origin: "https://claude.ai" }))).status).toBe(
      200,
    );
    expect((await app.request("/mcp", { method: "GET", headers: HEADERS })).status).toBe(405);
    expect((await app.request("/mcp", { method: "DELETE", headers: HEADERS })).status).toBe(405);
    const big = await app.request("/mcp", {
      method: "POST",
      headers: HEADERS,
      body: "x".repeat(300_000),
    });
    expect(big.status).toBe(413);
    const bad = await app.request("/mcp", { method: "POST", headers: HEADERS, body: "{nope" });
    expect(bad.status).toBe(400);
  });

  it("never forwards the secret past authentication and refuses oversized declared bodies", async () => {
    const { app } = setup();
    const declared = await app.request("/mcp", {
      method: "POST",
      headers: { ...HEADERS, "content-length": "999999" },
      body: "{}",
    });
    expect(declared.status).toBe(413);
  });

  it("returns a generic tool error without row data and reports the failure", async () => {
    const { app, store, errors, repo } = setup();
    store.fail = true;
    const res = await app.request("/mcp", post(call));
    const text = JSON.stringify(await payload(res));
    expect(text).not.toContain("PRIVATE ROW DATA");
    expect(text).toContain("isError");
    expect(errors).toHaveLength(1);
    expect(repo.audit).toHaveLength(0);
  });

  it("fails closed when the audit row cannot be written", async () => {
    const { app, repo } = setup();
    repo.failAudit = true;
    const res = await app.request("/mcp", post(call));
    const text = JSON.stringify(await payload(res));
    expect(text).not.toContain("Redfern");
    expect(text).toContain("isError");
  });

  it("rate limits per token and answers 503 when MCP is not configured", async () => {
    const limited = setup({ mcpRateLimitPerMinute: 1 });
    expect((await limited.app.request("/mcp", post(call))).status).toBe(200);
    expect((await limited.app.request("/mcp", post(call))).status).toBe(429);
    const off = testApp({ mcpTokenStore: new FakeTokens() });
    off.repo.addMember(WS, OWNER, "owner");
    expect((await off.app.request("/mcp", post(call))).status).toBe(503);
  });
});

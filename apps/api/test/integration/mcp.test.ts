/**
 * MCP end to end on local Supabase: a token created through the RPC reads as its user through RLS
 * (a session minted server-side), is audited as an mcp actor, and stops working once revoked.
 */
import { randomUUID } from "node:crypto";
import type { Database } from "@firra/db-types";
import { createClient } from "@supabase/supabase-js";
import { describe, expect, it } from "vitest";
import { createApp } from "../../src/app.js";
import { supabaseUserTokenVerifier } from "../../src/auth/tokens.js";
import { OrgService } from "../../src/connect/org.js";
import { ConnectService } from "../../src/connect/service.js";
import { SupabaseConnectStore } from "../../src/connect/store.js";
import { LocalKek } from "../../src/crypto/kek.js";
import { Keyring } from "../../src/crypto/keyring.js";
import { createLogger } from "../../src/lib/logger.js";
import { SupabaseReadStore } from "../../src/lib/read-store.js";
import { SupabaseRepository } from "../../src/lib/repository.js";
import { SupabaseMcpTokenAdmin } from "../../src/mcp/admin.js";
import { SupabaseMcpTokenStore, SupabaseSessionMinter } from "../../src/mcp/tokens.js";
import { SupabaseUploadConnectionStore } from "../../src/routes/uploads.js";
import { TaskService } from "../../src/tasks/enqueue.js";
import type { TaskQueue } from "../../src/tasks/queue.js";
import { SupabasePushStore } from "../../src/webhooks/push.js";

const SUPABASE_URL = process.env.SUPABASE_URL ?? "http://127.0.0.1:54321";
const SECRET_KEY = process.env.SUPABASE_SECRET_KEY ?? "";
const PUBLISHABLE_KEY = process.env.SUPABASE_PUBLISHABLE_KEY ?? "";
const LOGIN_PW = `pw-${randomUUID()}`;

const admin = createClient<Database>(SUPABASE_URL, SECRET_KEY, { auth: { persistSession: false } });

async function owner(): Promise<{ id: string; token: string }> {
  const email = `mcp-${randomUUID()}@example.com`;
  const { data, error: createError } = await admin.auth.admin.createUser({
    email,
    password: LOGIN_PW,
    email_confirm: true,
  });
  if (createError) throw new Error("test user setup failed");
  const anon = createClient(SUPABASE_URL, PUBLISHABLE_KEY, { auth: { persistSession: false } });
  const { data: session, error } = await anon.auth.signInWithPassword({
    email,
    password: LOGIN_PW,
  });
  if (error) throw new Error("test user setup failed");
  return { id: data.user.id, token: session.session.access_token };
}

async function seedClient(workspaceId: string, name: string, policy: "full" | "excluded") {
  const { data } = await admin
    .from("client")
    .insert({ workspace_id: workspaceId, name, policy })
    .select("id")
    .single();
  if (!data) throw new Error("no client");
  const today = new Date().toISOString().slice(0, 10);
  const { error } = await admin.from("account_assessment").insert({
    workspace_id: workspaceId,
    client_id: data.id,
    as_of: today,
    run_kind: "live",
    assessment_version: "test",
    rule_version: "test",
    risk_state: "high",
    state_since: today,
    expansion_state: "none",
    strength: "moderate",
    play: "fix_first",
    drivers: [],
  });
  if (error) throw new Error(`assessment insert failed: ${error.message}`);
}

function appFor() {
  const repo = new SupabaseRepository(admin);
  const logger = createLogger(() => undefined);
  const queue: TaskQueue = { enqueue: () => Promise.resolve() };
  const tasks = new TaskService(repo, queue);
  const store = new SupabaseConnectStore(admin);
  const keyring = new Keyring(new LocalKek(Buffer.alloc(32, 1)), store);
  return createApp({
    logger,
    repo,
    tasks,
    connect: new ConnectService({ store, keyring, tasks, logger, google: null }),
    org: new OrgService({
      store,
      keyring,
      tasks,
      logger,
      googleDelegationClientId: null,
      microsoft: null,
      microsoftAdminRedirectUri: "http://localhost/oauth/microsoft/admin-callback",
    }),
    webUrl: "http://localhost:3000",
    verifyUser: supabaseUserTokenVerifier(admin),
    verifyInternal: () => Promise.resolve(null),
    webhooks: [],
    checkReady: () => Promise.resolve(true),
    captureException: () => undefined,
    rateLimitPerMinute: 1000,
    userRateLimitPerMinute: 1000,
    release: "test",
    pushStore: new SupabasePushStore(admin),
    uploads: null,
    uploadConnections: new SupabaseUploadConnectionStore(admin),
    readStore: new SupabaseReadStore(SUPABASE_URL, PUBLISHABLE_KEY),
    mcpTokens: new SupabaseMcpTokenAdmin(SUPABASE_URL, PUBLISHABLE_KEY),
    vaultStore: null,
    setupStore: null,
    meStore: null,
    mcpTokenStore: new SupabaseMcpTokenStore(admin),
    sessionMinter: new SupabaseSessionMinter(admin, SUPABASE_URL, PUBLISHABLE_KEY),
    mcpAllowedOrigins: [],
    mcpRateLimitPerMinute: 1000,
  });
}

const rpc = (secret: string) => ({
  method: "POST",
  headers: {
    "content-type": "application/json",
    accept: "application/json, text/event-stream",
    "mcp-protocol-version": "2025-11-25",
    authorization: `Bearer ${secret}`,
  },
  body: JSON.stringify({
    jsonrpc: "2.0",
    id: 1,
    method: "tools/call",
    params: { name: "get_clients_needing_attention", arguments: {} },
  }),
});

describe("MCP endpoint (local Supabase)", () => {
  it("reads as the token's user, audits as mcp, and stops after revocation", async () => {
    const { data: ws } = await admin
      .from("workspace")
      .insert({ name: "MCP IT" })
      .select("id")
      .single();
    if (!ws) throw new Error("no workspace");
    const user = await owner();
    await admin.from("membership").insert({ workspace_id: ws.id, user_id: user.id, role: "owner" });
    await seedClient(ws.id, "Visible Full", "full");
    await seedClient(ws.id, "Hidden Excluded", "excluded");
    const app = appFor();

    const created = await app.request(`/v1/workspaces/${ws.id}/mcp/tokens`, {
      method: "POST",
      headers: { Authorization: `Bearer ${user.token}`, "content-type": "application/json" },
      body: JSON.stringify({ name: "it" }),
    });
    expect(created.status).toBe(201);
    const { id, token } = (await created.json()) as { id: string; token: string };
    expect(token).toMatch(/^frm_[0-9a-f]{64}$/);

    const res = await app.request("/mcp", rpc(token));
    expect(res.status).toBe(200);
    const text = await res.text();
    expect(text).toContain("Visible Full");
    expect(text).not.toContain("Hidden Excluded");

    const { data: audit } = await admin
      .from("audit_log")
      .select("actor_type, actor_id, action")
      .eq("workspace_id", ws.id)
      .eq("action", "mcp.get_clients_needing_attention");
    expect(audit).toEqual([
      { actor_type: "mcp", actor_id: id, action: "mcp.get_clients_needing_attention" },
    ]);

    const revoked = await app.request(`/v1/workspaces/${ws.id}/mcp/tokens/${id}`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${user.token}` },
    });
    expect(revoked.status).toBe(204);
    expect((await app.request("/mcp", rpc(token))).status).toBe(401);
  });
});

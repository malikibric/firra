/**
 * P0 acceptance (local): user token -> api -> task queue -> worker -> DB.
 * Needs `supabase start`; spawns the worker itself. Run: pnpm --filter @firra/api test:integration
 */
import { type ChildProcess, spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { resolve } from "node:path";
import type { Database } from "@firra/db-types";
import { createClient } from "@supabase/supabase-js";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createApp } from "../../src/app.js";
import { supabaseUserTokenVerifier } from "../../src/auth/tokens.js";
import { createLogger } from "../../src/lib/logger.js";
import { SupabaseRepository } from "../../src/lib/repository.js";
import { OrgService } from "../../src/connect/org.js";
import { ConnectService } from "../../src/connect/service.js";
import { SupabaseConnectStore } from "../../src/connect/store.js";
import { LocalKek } from "../../src/crypto/kek.js";
import { Keyring } from "../../src/crypto/keyring.js";
import { TaskService } from "../../src/tasks/enqueue.js";
import { SupabasePushStore } from "../../src/webhooks/push.js";
import { SupabaseUploadConnectionStore } from "../../src/routes/uploads.js";
import { LocalDispatcher } from "../../src/tasks/local-dispatcher.js";

const SUPABASE_URL = process.env.SUPABASE_URL ?? "http://127.0.0.1:54321";
// Well-known local-only keys printed by `supabase start` (not secrets).
const SECRET_KEY = process.env.SUPABASE_SECRET_KEY ?? "";
const PUBLISHABLE_KEY =
  process.env.SUPABASE_PUBLISHABLE_KEY ?? "";
const DB_URL =
  process.env.FIRRA_DATABASE_URL ?? "postgresql://postgres:postgres@127.0.0.1:54322/postgres";
const TASK_SECRET = "integration-test-secret-32-chars-min!!";
const WORKER_PORT = 18081;
const WORKER_URL = `http://127.0.0.1:${String(WORKER_PORT)}`;

let worker: ChildProcess | undefined;

async function waitFor(url: string, timeoutMs = 30_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      if ((await fetch(url)).ok) return;
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`timed out waiting for ${url}`);
}

beforeAll(async () => {
  worker = spawn(
    "uv",
    ["run", "uvicorn", "--factory", "firra.app:create_app", "--port", String(WORKER_PORT)],
    {
      cwd: resolve(import.meta.dirname, "../../../../services/worker"),
      env: {
        ...process.env,
        FIRRA_ENV: "test",
        FIRRA_DATABASE_URL: DB_URL,
        FIRRA_TASK_AUTH_MODE: "local_secret",
        FIRRA_LOCAL_TASK_SECRET: TASK_SECRET,
        FIRRA_KMS_MODE: "local",
        FIRRA_LOCAL_KEK_HEX: "00".repeat(31) + "01",
        FIRRA_LOCAL_RAW_DIR: resolve(import.meta.dirname, "../../../../.local/raw"),
      },
      stdio: "ignore",
    },
  );
  await waitFor(`${WORKER_URL}/ready`);
}, 60_000);

afterAll(() => {
  worker?.kill("SIGTERM");
});

describe("task round-trip", () => {
  it("owner triggers diagnostics.ping and the worker marks the job succeeded", async () => {
    const admin = createClient<Database>(SUPABASE_URL, SECRET_KEY, {
      auth: { persistSession: false },
    });
    const email = `roundtrip-${randomUUID()}@example.com`;
    const password = `pw-${randomUUID()}`;
    const created = await admin.auth.admin.createUser({ email, password, email_confirm: true });
    expect(created.error).toBeNull();

    const userClient = createClient<Database>(SUPABASE_URL, PUBLISHABLE_KEY, {
      auth: { persistSession: false },
    });
    const session = await userClient.auth.signInWithPassword({ email, password });
    const token = session.data.session?.access_token;
    if (!token) throw new Error("sign-in failed");
    const workspace = await userClient.rpc("create_workspace", { p_name: "Roundtrip" });
    expect(workspace.error).toBeNull();
    const workspaceId = workspace.data;
    if (!workspaceId) throw new Error("no workspace");

    const repo = new SupabaseRepository(admin);
    const logger = createLogger(() => undefined);
    const tasks = new TaskService(repo, new LocalDispatcher(WORKER_URL, TASK_SECRET, logger));
    const store = new SupabaseConnectStore(admin);
    const app = createApp({
      logger,
      repo,
      tasks,
      connect: new ConnectService({
        store,
        keyring: new Keyring(new LocalKek(Buffer.alloc(32, 1)), store),
        tasks,
        logger,
        google: null,
      }),
      webUrl: "http://localhost:3000",
      verifyUser: supabaseUserTokenVerifier(admin),
      verifyInternal: () => Promise.resolve(null),
      webhooks: [],
      checkReady: () => Promise.resolve(true),
      captureException: () => undefined,
      rateLimitPerMinute: 100,
      readStore: null,
      mcpTokens: null,
      vaultStore: null,
      setupStore: null,
      meStore: null,
      mcpTokenStore: null,
      sessionMinter: null,
      mcpAllowedOrigins: [],
      mcpRateLimitPerMinute: 100,
      userRateLimitPerMinute: 100,
      release: "test",
      pushStore: new SupabasePushStore(admin),
      uploads: null,
      uploadConnections: new SupabaseUploadConnectionStore(admin),
      org: new OrgService({
        store,
        keyring: new Keyring(new LocalKek(Buffer.alloc(32, 1)), store),
        tasks,
        logger,
        googleDelegationClientId: null,
        microsoft: null,
        microsoftAdminRedirectUri: "http://127.0.0.1:8080/oauth/microsoft/admin-callback",
      }),
    });

    const response = await app.request(`/v1/workspaces/${workspaceId}/diagnostics/ping`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.status).toBe(202);
    const { id: jobId } = (await response.json()) as { id: string };

    const status = await app.request(`/v1/workspaces/${workspaceId}/jobs/${jobId}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const job = (await status.json()) as {
      status: string;
      attempts: number;
      progress: Record<string, unknown>;
    };
    expect(job.status).toBe("succeeded");
    expect(job.attempts).toBe(1);
    expect(job.progress).toHaveProperty("pong_at");

    // The user can read the job through RLS as well.
    const viaRls = await userClient.from("job").select("status").eq("id", jobId).single();
    expect(viaRls.data?.status).toBe("succeeded");

    await admin.from("workspace").delete().eq("id", workspaceId);
    await admin.auth.admin.deleteUser(created.data.user?.id ?? "");
  }, 60_000);
});

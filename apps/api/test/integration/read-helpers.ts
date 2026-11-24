/** Shared local Supabase fixtures for caller-token read integration tests. */
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import type { Database } from "@firra/db-types";
import { createClient } from "@supabase/supabase-js";
import { createApp } from "../../src/app.js";
import { supabaseUserTokenVerifier } from "../../src/auth/tokens.js";
import { OrgService } from "../../src/connect/org.js";
import { ConnectService } from "../../src/connect/service.js";
import { SupabaseConnectStore } from "../../src/connect/store.js";
import { LocalKek } from "../../src/crypto/kek.js";
import { Keyring } from "../../src/crypto/keyring.js";
import { createLogger } from "../../src/lib/logger.js";
import { SupabaseVaultStore } from "../../src/lib/vault-store.js";
import { SupabaseSetupStore } from "../../src/lib/setup-store.js";
import { SupabaseMeStore } from "../../src/lib/me-store.js";
import { SupabaseReadStore } from "../../src/lib/read-store.js";
import { SupabaseRepository } from "../../src/lib/repository.js";
import { TaskService } from "../../src/tasks/enqueue.js";
import type { TaskQueue } from "../../src/tasks/queue.js";
import { SupabasePushStore } from "../../src/webhooks/push.js";
import { SupabaseUploadConnectionStore } from "../../src/routes/uploads.js";

export const SUPABASE_URL = process.env.SUPABASE_URL ?? "http://127.0.0.1:54321";
const SECRET_KEY = process.env.SUPABASE_SECRET_KEY ?? "";
export const PUBLISHABLE_KEY = process.env.SUPABASE_PUBLISHABLE_KEY ?? "";
const LOGIN_PW = `pw-${randomUUID()}`;

export const admin = createClient<Database>(SUPABASE_URL, SECRET_KEY, {
  auth: { persistSession: false },
});

export async function userWithToken(
  prefix: string,
): Promise<{ id: string; token: string; email: string }> {
  const email = `${prefix}-${randomUUID()}@example.com`;
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
  return { id: data.user.id, token: session.session.access_token, email };
}

export async function workspace(): Promise<string> {
  const { data } = await admin.from("workspace").insert({ name: "Read IT" }).select("id").single();
  if (!data) throw new Error("no workspace");
  return data.id;
}

export function appFor(): ReturnType<typeof createApp> {
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
    mcpTokens: null,
    vaultStore: new SupabaseVaultStore(admin),
    setupStore: new SupabaseSetupStore(SUPABASE_URL, PUBLISHABLE_KEY),
    meStore: new SupabaseMeStore(SUPABASE_URL, PUBLISHABLE_KEY),
    mcpTokenStore: null,
    sessionMinter: null,
    mcpAllowedOrigins: [],
    mcpRateLimitPerMinute: 1000,
    pushStore: new SupabasePushStore(admin),
    uploads: null,
    uploadConnections: new SupabaseUploadConnectionStore(admin),
    readStore: new SupabaseReadStore(SUPABASE_URL, PUBLISHABLE_KEY),
  });
}

const DB_URL =
  process.env.FIRRA_DATABASE_URL ?? "postgresql://postgres:postgres@127.0.0.1:54322/postgres";

/**
 * Runs fixture SQL in one transaction with deferred constraints. Rows that must carry evidence
 * (commitments, goals, needs) cannot be inserted through PostgREST, where every insert commits on
 * its own before its evidence link exists.
 */
export function sqlFixture(sql: string): void {
  execFileSync(
    "psql",
    [DB_URL, "-v", "ON_ERROR_STOP=1", "-q", "-1", "-c", `set constraints all deferred; ${sql}`],
    {
      stdio: ["ignore", "ignore", "pipe"],
    },
  );
}

/** One verified-evidence chain (connection, source record, evidence) for a client; returns the evidence id. */
export function seedEvidenceSql(ws: string, clientId: string, evidenceVar = "e"): string {
  return `
    insert into public.connection (workspace_id, provider, mode, account_label)
      values ('${ws}', 'email_upload', 'upload', 'fixture-${randomUUID()}') returning id into c;
    insert into public.source_record (workspace_id, connection_id, provider, external_id, kind, client_id,
        policy_at_ingest, occurred_at, direction, word_count, body_clean)
      values ('${ws}', c, 'email_upload', 'fixture-${randomUUID()}', 'email', '${clientId}', 'full',
        '2025-01-01T00:00:00Z', 'in', 12, 'We are two weeks late on the report.') returning id into r;
    insert into public.evidence (workspace_id, source_record_id, client_id, quote, locator)
      values ('${ws}', r, '${clientId}', 'we are two weeks late', '{"start":3,"end":24}')
      returning id into ${evidenceVar};`;
}

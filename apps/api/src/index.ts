import { serve } from "@hono/node-server";
import * as Sentry from "@sentry/node";
import type { Database } from "@firra/db-types";
import { createClient } from "@supabase/supabase-js";
import { createApp } from "./app.js";
import { OrgService } from "./connect/org.js";
import { ConnectService } from "./connect/service.js";
import { SupabaseConnectStore } from "./connect/store.js";
import { CloudKmsKek, LocalKek } from "./crypto/kek.js";
import { Keyring } from "./crypto/keyring.js";
import { googleIdTokenVerifier, supabaseUserTokenVerifier } from "./auth/tokens.js";
import { loadEnv } from "./env.js";
import { createLogger } from "./lib/logger.js";
import { SupabaseVaultStore } from "./lib/vault-store.js";
import { SupabaseSetupStore } from "./lib/setup-store.js";
import { inviteEmailSender } from "./lib/invite-email.js";
import { SupabaseMeStore } from "./lib/me-store.js";
import { SupabaseMcpTokenAdmin } from "./mcp/admin.js";
import { SupabaseMcpTokenStore, SupabaseSessionMinter } from "./mcp/tokens.js";
import { SupabaseReadStore } from "./lib/read-store.js";
import { SupabaseRepository } from "./lib/repository.js";
import { gmailPushProvider, SupabasePushStore } from "./webhooks/push.js";
import { SupabaseUploadConnectionStore } from "./routes/uploads.js";
import { GcsUploadStorage, LocalUploadStorage } from "./uploads/storage.js";
import { CloudTasksQueue } from "./tasks/cloud-tasks.js";
import { TaskService } from "./tasks/enqueue.js";
import { LocalDispatcher } from "./tasks/local-dispatcher.js";
import type { TaskQueue } from "./tasks/queue.js";

const env = loadEnv();
const logger = createLogger();

if (env.SENTRY_DSN) {
  Sentry.init({
    dsn: env.SENTRY_DSN,
    environment: env.FIRRA_ENV,
    release: env.RELEASE,
    // Collect no user info, headers, cookies, bodies, query params or query data.
    dataCollection: {
      userInfo: false,
      cookies: false,
      httpHeaders: false,
      httpBodies: [],
      urlQueryParams: false,
      genAI: { inputs: false, outputs: false },
      databaseQueryData: false,
    },
    tracesSampleRate: 0,
    beforeSend(event) {
      delete event.user;
      if (event.request) {
        delete event.request.data;
        delete event.request.cookies;
        delete event.request.query_string;
        delete event.request.headers;
      }
      return event;
    },
  });
}

// Service-role client: server-side only, never exposed to browsers.
const supabase = createClient<Database>(env.SUPABASE_URL, env.SUPABASE_SECRET_KEY, {
  auth: { persistSession: false, autoRefreshToken: false },
});
const repo = new SupabaseRepository(supabase);
const connectStore = new SupabaseConnectStore(supabase);
const kek =
  env.KMS_MODE === "local" && env.LOCAL_KEK_HEX
    ? new LocalKek(Buffer.from(env.LOCAL_KEK_HEX, "hex"))
    : new CloudKmsKek(env.KMS_KEY_NAME ?? "");

function createQueue(): TaskQueue {
  if (env.TASK_QUEUE_MODE === "local") {
    return new LocalDispatcher(env.WORKER_URL, env.LOCAL_TASK_SECRET ?? "", logger);
  }
  return new CloudTasksQueue({
    projectId: env.GCP_PROJECT_ID ?? "",
    region: env.GCP_REGION,
    workerUrl: env.WORKER_URL,
    invokerServiceAccount: env.TASKS_INVOKER_SA_EMAIL ?? "",
  });
}

const tasks = new TaskService(repo, createQueue());
const microsoftConfig =
  env.MICROSOFT_CLIENT_ID && env.MICROSOFT_CLIENT_SECRET
    ? {
        clientId: env.MICROSOFT_CLIENT_ID,
        clientSecret: env.MICROSOFT_CLIENT_SECRET,
        redirectUri: `${env.API_URL}/oauth/microsoft/callback`,
        tenant: env.MICROSOFT_TENANT,
      }
    : null;
const keyring = new Keyring(kek, connectStore);
const connect = new ConnectService({
  store: connectStore,
  keyring,
  tasks,
  logger,
  google:
    env.GOOGLE_CLIENT_ID && env.GOOGLE_CLIENT_SECRET
      ? {
          clientId: env.GOOGLE_CLIENT_ID,
          clientSecret: env.GOOGLE_CLIENT_SECRET,
          redirectUri: `${env.API_URL}/oauth/google/callback`,
        }
      : null,
  microsoft: microsoftConfig,
});

const pushStore = new SupabasePushStore(supabase);
const webhooks = env.GMAIL_PUSH_SA_EMAIL
  ? [
      gmailPushProvider({
        tasks,
        store: pushStore,
        verifyIdToken: googleIdTokenVerifier(`${env.API_URL}/webhooks/gmail`, [
          env.GMAIL_PUSH_SA_EMAIL,
        ]),
      }),
    ]
  : [];

const uploads = env.LOCAL_RAW_DIR
  ? new LocalUploadStorage(env.LOCAL_RAW_DIR, env.API_URL)
  : env.RAW_BUCKET
    ? new GcsUploadStorage(env.RAW_BUCKET)
    : null;

const org = new OrgService({
  store: connectStore,
  keyring,
  tasks,
  logger,
  googleDelegationClientId: env.WORKER_SA_CLIENT_ID ?? null,
  microsoft: microsoftConfig,
  microsoftAdminRedirectUri: `${env.API_URL}/oauth/microsoft/admin-callback`,
});

const app = createApp({
  logger,
  repo,
  tasks,
  connect,
  webUrl: env.WEB_URL,
  verifyUser: supabaseUserTokenVerifier(supabase),
  verifyInternal: googleIdTokenVerifier(env.API_URL, env.INTERNAL_CALLER_SA_EMAILS),
  webhooks,
  pushStore,
  uploads,
  uploadConnections: new SupabaseUploadConnectionStore(supabase),
  org,
  readStore: env.SUPABASE_PUBLISHABLE_KEY
    ? new SupabaseReadStore(env.SUPABASE_URL, env.SUPABASE_PUBLISHABLE_KEY)
    : null,
  vaultStore: new SupabaseVaultStore(supabase),
  setupStore: env.SUPABASE_PUBLISHABLE_KEY
    ? new SupabaseSetupStore(
        env.SUPABASE_URL,
        env.SUPABASE_PUBLISHABLE_KEY,
        inviteEmailSender(env.RESEND_API_KEY, env.EMAIL_FROM),
      )
    : null,
  meStore: env.SUPABASE_PUBLISHABLE_KEY
    ? new SupabaseMeStore(env.SUPABASE_URL, env.SUPABASE_PUBLISHABLE_KEY)
    : null,
  mcpTokens: env.SUPABASE_PUBLISHABLE_KEY
    ? new SupabaseMcpTokenAdmin(env.SUPABASE_URL, env.SUPABASE_PUBLISHABLE_KEY)
    : null,
  mcpTokenStore: env.SUPABASE_PUBLISHABLE_KEY ? new SupabaseMcpTokenStore(supabase) : null,
  sessionMinter: env.SUPABASE_PUBLISHABLE_KEY
    ? new SupabaseSessionMinter(supabase, env.SUPABASE_URL, env.SUPABASE_PUBLISHABLE_KEY)
    : null,
  mcpAllowedOrigins: env.MCP_ALLOWED_ORIGINS,
  mcpRateLimitPerMinute: env.MCP_RATE_LIMIT_PER_MINUTE,
  userRateLimitPerMinute: env.USER_RATE_LIMIT_PER_MINUTE,
  release: env.RELEASE,
  checkReady: async () => {
    const response = await fetch(`${env.SUPABASE_URL}/auth/v1/health`, {
      headers: { apikey: env.SUPABASE_SECRET_KEY },
      signal: AbortSignal.timeout(3_000),
    });
    return response.ok;
  },
  captureException: (error) => Sentry.captureException(error),
  rateLimitPerMinute: env.RATE_LIMIT_PER_MINUTE,
});

const server = serve({ fetch: app.fetch, port: env.PORT }, (info) => {
  logger.info("api.started", { env: env.FIRRA_ENV, release: env.RELEASE, port: info.port });
});

process.on("SIGTERM", () => {
  server.close(() => process.exit(0));
});

import { Hono } from "hono";
import { secureHeaders } from "hono/secure-headers";
import type { IdTokenVerifier, UserTokenVerifier } from "./auth/tokens.js";
import type { Logger } from "./lib/logger.js";
import type { ReadStore } from "./lib/read-store.js";
import type { ApiRepository } from "./lib/repository.js";
import type { AppEnv } from "./lib/types.js";
import { rateLimit } from "./middleware/rate-limit.js";
import { requestLog } from "./middleware/request-log.js";
import { v1Envelope } from "./middleware/v1-envelope.js";
import { readRoutes } from "./routes/read.js";
import { mcpTokenRoutes } from "./routes/mcp-tokens.js";
import { mcpRoutes } from "./routes/mcp.js";
import { contributorRoutes } from "./routes/contributor.js";
import { setupRoutes } from "./routes/setup.js";
import type { SetupStore } from "./lib/setup-store.js";
import type { MeStore } from "./lib/me-store.js";
import type { McpTokenStore, SessionMinter } from "./mcp/tokens.js";
import type { McpTokenAdmin } from "./mcp/admin.js";
import { internalDiagnosticsRoutes, userDiagnosticsRoutes } from "./routes/diagnostics.js";
import type { ConnectService } from "./connect/service.js";
import type { OrgService } from "./connect/org.js";
import { memberConnectRoutes, publicConnectRoutes } from "./routes/connect.js";
import { orgCallbackRoutes, orgRoutes } from "./routes/org.js";
import { localUploadRoutes, uploadRoutes, type UploadConnectionStore } from "./routes/uploads.js";
import { LocalUploadStorage, type UploadStorage } from "./uploads/storage.js";
import type { TaskService } from "./tasks/enqueue.js";
import { microsoftPushRoutes, type PushStore } from "./webhooks/push.js";
import { webhookRoutes } from "./webhooks/route.js";
import type { WebhookProvider } from "./webhooks/verifier.js";

import type { VaultStore } from "./lib/vault-store.js";
import { vaultRoutes } from "./routes/vault.js";

export interface AppDeps {
  logger: Logger;
  repo: ApiRepository;
  tasks: TaskService;
  verifyUser: UserTokenVerifier;
  verifyInternal: IdTokenVerifier;
  webhooks: readonly WebhookProvider[];
  checkReady: () => Promise<boolean>;
  captureException: (error: unknown) => void;
  rateLimitPerMinute: number;
  connect: ConnectService;
  webUrl: string | undefined;
  pushStore: PushStore;
  uploads: UploadStorage | null;
  uploadConnections: UploadConnectionStore;
  org: OrgService;
  readStore: ReadStore | null;
  mcpTokens: McpTokenAdmin | null;
  meStore: MeStore | null;
  vaultStore: VaultStore | null;
  setupStore: SetupStore | null;
  mcpTokenStore: McpTokenStore | null;
  sessionMinter: SessionMinter | null;
  mcpAllowedOrigins: readonly string[];
  mcpRateLimitPerMinute: number;
  userRateLimitPerMinute: number;
  release: string;
}

export function createApp(deps: AppDeps): Hono<AppEnv> {
  const app = new Hono<AppEnv>();

  app.use("*", secureHeaders());
  app.use("*", requestLog(deps.logger));
  app.use("/v1/*", v1Envelope());
  app.use("/v1/*", rateLimit(deps.rateLimitPerMinute));
  app.use("/webhooks/*", rateLimit(deps.rateLimitPerMinute));
  app.use("/mcp", rateLimit(deps.rateLimitPerMinute));
  app.use("/connect/*", rateLimit(deps.rateLimitPerMinute));
  app.use("/oauth/*", rateLimit(deps.rateLimitPerMinute));

  // Cloud Run reserves paths ending in "z" (e.g. /healthz), so probes use /health and /ready.
  app.get("/health", (c) => c.json({ status: "ok" }));
  app.get("/ready", async (c) => {
    const ready = await deps.checkReady().catch(() => false);
    return c.json({ status: ready ? "ok" : "unavailable" }, ready ? 200 : 503);
  });

  app.route("/v1", memberConnectRoutes(deps));
  app.route("/v1", userDiagnosticsRoutes(deps));
  app.route("/v1", uploadRoutes(deps));
  app.route("/v1", orgRoutes(deps));
  app.route("/v1", readRoutes(deps));
  app.route("/v1", mcpTokenRoutes(deps));
  app.route("/v1", contributorRoutes(deps));
  app.route("/v1", vaultRoutes(deps));
  app.route("/v1", setupRoutes(deps));
  app.route("/", mcpRoutes(deps));
  app.route("/", orgCallbackRoutes(deps));
  if (deps.uploads instanceof LocalUploadStorage)
    app.route("/", localUploadRoutes(deps.uploads, deps.webUrl));
  app.route("/", publicConnectRoutes(deps));
  app.route("/internal/v1", internalDiagnosticsRoutes(deps));
  app.route("/webhooks", microsoftPushRoutes({ tasks: deps.tasks, store: deps.pushStore }));
  app.route("/webhooks", webhookRoutes(deps.webhooks));

  app.notFound((c) => c.json({ error: "not_found" }, 404));
  app.onError((error, c) => {
    deps.captureException(error);
    deps.logger.error("http.unhandled_error", { method: c.req.method, err: error });
    return c.json({ error: "internal_error" }, 500);
  });

  return app;
}

import { Hono } from "hono";
import { clientIp } from "../lib/client-ip.js";
import type { ReadStore } from "../lib/read-store.js";
import type { ApiRepository } from "../lib/repository.js";
import type { AppEnv } from "../lib/types.js";
import { createMcpFetch } from "../mcp/server.js";
import type { McpTokenStore, SessionMinter } from "../mcp/tokens.js";
import { rateLimit } from "../middleware/rate-limit.js";

interface Deps {
  repo: ApiRepository;
  readStore: ReadStore | null;
  mcpTokenStore: McpTokenStore | null;
  sessionMinter: SessionMinter | null;
  mcpAllowedOrigins: readonly string[];
  mcpRateLimitPerMinute: number;
  captureException: (error: unknown) => void;
  now?: () => Date;
}

const MAX_BODY_BYTES = 256 * 1024;
const INET = /^[0-9a-fA-F:.]{2,45}$/;
const BEARER = /^Bearer\s+(\S+)$/i;

/**
 * Remote MCP endpoint (docs/plans/f-mcp.md): one stateless POST route, personal access token in
 * the Authorization header, token-bound workspace, read-only tools over the `/v1` read models.
 */
export function mcpRoutes(deps: Deps): Hono<AppEnv> {
  const app = new Hono<AppEnv>();
  const now = deps.now ?? (() => new Date());
  const serve =
    deps.readStore && deps.sessionMinter
      ? createMcpFetch({
          readStore: deps.readStore,
          minter: deps.sessionMinter,
          repo: deps.repo,
          onError: deps.captureException,
          now,
        })
      : null;
  const perToken = rateLimit(deps.mcpRateLimitPerMinute, Date.now, (c) => c.get("mcpTokenId"));

  app.all("/mcp", async (c, next) => {
    // DNS rebinding defence: a browser origin must be allowlisted; clients without one pass.
    const origin = c.req.header("origin");
    if (origin !== undefined && !deps.mcpAllowedOrigins.includes(origin)) {
      return c.json({ error: "forbidden_origin" }, 403);
    }
    if (c.req.method !== "POST") {
      c.header("Allow", "POST");
      return c.json({ error: "method_not_allowed" }, 405);
    }
    const secret = BEARER.exec(c.req.header("authorization") ?? "")?.[1];
    const token =
      secret && deps.mcpTokenStore ? await deps.mcpTokenStore.resolve(secret, now()) : null;
    if (!token) {
      c.header("WWW-Authenticate", 'Bearer realm="firra"');
      return c.json({ error: "unauthorized" }, 401);
    }
    // Only an owner or admin may keep using a token (the membership can have changed).
    const role = await deps.repo.getMembershipRole(token.workspaceId, token.userId);
    if (role !== "owner" && role !== "admin") {
      c.header("WWW-Authenticate", 'Bearer realm="firra"');
      return c.json({ error: "unauthorized" }, 401);
    }
    c.set("mcpTokenId", token.id);
    c.set("mcpToken", token);
    await next();
  });
  app.all("/mcp", perToken);

  app.post("/mcp", async (c) => {
    if (!serve) return c.json({ error: "mcp_not_configured" }, 503);
    // Refuse before buffering when the declared size is already too large.
    if (Number(c.req.header("content-length") ?? 0) > MAX_BODY_BYTES) {
      return c.json({ error: "payload_too_large" }, 413);
    }
    const text = await c.req.text();
    if (new TextEncoder().encode(text).length > MAX_BODY_BYTES) {
      return c.json({ error: "payload_too_large" }, 413);
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch {
      return c.json({ error: "invalid_json" }, 400);
    }
    const token = c.get("mcpToken");
    const ip = clientIp(c.req.header("x-forwarded-for"));
    // The secret was only needed to resolve the token; it goes no further than this point.
    const headers = new Headers(c.req.raw.headers);
    headers.delete("authorization");
    const request = new Request(c.req.url, { method: "POST", headers, body: text });
    return serve(
      request,
      {
        tokenId: token.id,
        workspaceId: token.workspaceId,
        userId: token.userId,
        scopes: token.scopes,
        ip: INET.test(ip) ? ip : null,
        userAgent: c.req.header("user-agent") ?? null,
      },
      parsed,
    );
  });

  return app;
}

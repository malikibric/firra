import { createMcpTokenRequest, mcpTokenCreated, mcpTokensResponse } from "@firra/contracts";
import { Hono } from "hono";
import type { UserTokenVerifier } from "../auth/tokens.js";
import type { ApiRepository } from "../lib/repository.js";
import type { AppEnv } from "../lib/types.js";
import { TokenForbiddenError, TooManyTokensError, type McpTokenAdmin } from "../mcp/admin.js";
import { apiFailure } from "../middleware/v1-envelope.js";
import { v1Guard } from "./v1-guard.js";

interface Deps {
  repo: ApiRepository;
  verifyUser: UserTokenVerifier;
  userRateLimitPerMinute: number;
  mcpTokens: McpTokenAdmin | null;
}

/**
 * MCP personal access tokens under /v1/workspaces/:workspaceId/mcp/tokens (owner/admin). The
 * RPCs run as the caller, authorize and write the audit rows; the secret is returned once.
 */
export function mcpTokenRoutes(deps: Deps): Hono<AppEnv> {
  const app = new Hono<AppEnv>();
  const chain = v1Guard(deps);
  const bearer = (header: string | undefined) => header?.slice("Bearer ".length).trim() ?? "";

  app.post("/workspaces/:workspaceId/mcp/tokens", ...chain, async (c) => {
    if (!deps.mcpTokens) {
      return apiFailure(c, 503, "reads_not_configured", "MCP tokens are not configured here.");
    }
    const body = createMcpTokenRequest.safeParse(await c.req.json().catch(() => null));
    if (!body.success) return apiFailure(c, 422, "invalid_request", "Name is required.");
    try {
      const created = await deps.mcpTokens.create(
        { token: bearer(c.req.header("authorization")), userId: c.get("user").id },
        c.req.param("workspaceId"),
        body.data,
      );
      return c.json(mcpTokenCreated.parse(created), 201);
    } catch (error) {
      if (error instanceof TokenForbiddenError)
        return apiFailure(c, 404, "not_found", "Not found.");
      if (error instanceof TooManyTokensError) {
        return apiFailure(c, 422, "invalid_request", "Too many active tokens; revoke one first.");
      }
      throw error;
    }
  });

  app.get("/workspaces/:workspaceId/mcp/tokens", ...chain, async (c) => {
    if (!deps.mcpTokens) {
      return apiFailure(c, 503, "reads_not_configured", "MCP tokens are not configured here.");
    }
    const items = await deps.mcpTokens.list(
      { token: bearer(c.req.header("authorization")), userId: c.get("user").id },
      c.req.param("workspaceId"),
    );
    return c.json(mcpTokensResponse.parse({ items }));
  });

  app.delete("/workspaces/:workspaceId/mcp/tokens/:tokenId", ...chain, async (c) => {
    if (!deps.mcpTokens) {
      return apiFailure(c, 503, "reads_not_configured", "MCP tokens are not configured here.");
    }
    try {
      await deps.mcpTokens.revoke(
        { token: bearer(c.req.header("authorization")), userId: c.get("user").id },
        c.req.param("workspaceId"),
        c.req.param("tokenId"),
      );
      return c.body(null, 204);
    } catch (error) {
      if (error instanceof TokenForbiddenError)
        return apiFailure(c, 404, "not_found", "Not found.");
      throw error;
    }
  });

  return app;
}

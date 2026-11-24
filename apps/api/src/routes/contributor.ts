import {
  authorizationUrlResponse,
  claimInviteRequest,
  claimInviteResponse,
  contributorConnectionsResponse,
  contributorConnectStart,
  meResponse,
} from "@firra/contracts";
import { Hono } from "hono";
import { z } from "zod";
import type { UserTokenVerifier } from "../auth/tokens.js";
import { safeReturnPath } from "../connect/pkce.js";
import { ProviderNotConfiguredError, type ConnectService } from "../connect/service.js";
import { MeNotFoundError, type MeStore } from "../lib/me-store.js";
import type { ReadCaller } from "../lib/read-store.js";
import type { AppEnv } from "../lib/types.js";
import { apiFailure } from "../middleware/v1-envelope.js";
import { v1UserGuard } from "./v1-guard.js";

interface Deps {
  verifyUser: UserTokenVerifier;
  userRateLimitPerMinute: number;
  meStore: MeStore | null;
  connect: ConnectService;
}

const provider = z.enum(["google", "microsoft"]);

/**
 * What any signed-in user may do about themselves: see their workspaces, claim a contributor
 * invite, and connect or disconnect their own sources. Contributors see nothing else (no workspace
 * role is needed or granted here; docs/plans/d-mac-app.md D-D3).
 */
export function contributorRoutes(deps: Deps): Hono<AppEnv> {
  const app = new Hono<AppEnv>();
  const chain = v1UserGuard(deps);
  const caller = (c: {
    get: (key: "user") => { id: string };
    req: { header: (n: string) => string | undefined };
  }): ReadCaller => ({
    userId: c.get("user").id,
    token: c.req.header("authorization")?.slice("Bearer ".length).trim() ?? "",
  });

  app.get("/me", ...chain, async (c) => {
    if (!deps.meStore) return apiFailure(c, 503, "reads_not_configured", "Not configured here.");
    const data = await deps.meStore.me(caller(c));
    const user = c.get("user");
    return c.json(meResponse.parse({ user: { id: user.id, email: user.email }, ...data }));
  });

  app.post("/contributor/claim", ...chain, async (c) => {
    if (!deps.meStore) return apiFailure(c, 503, "reads_not_configured", "Not configured here.");
    const body = claimInviteRequest.safeParse(await c.req.json().catch(() => null));
    if (!body.success)
      return apiFailure(c, 422, "invalid_request", "A valid invite code is required.");
    try {
      return c.json(claimInviteResponse.parse(await deps.meStore.claim(caller(c), body.data)));
    } catch (error) {
      if (error instanceof MeNotFoundError)
        return apiFailure(c, 404, "not_found", "Invite not found.");
      throw error;
    }
  });

  app.get("/workspaces/:workspaceId/contributor/connections", ...chain, async (c) => {
    if (!deps.meStore) return apiFailure(c, 503, "reads_not_configured", "Not configured here.");
    const items = await deps.meStore.connections(caller(c), c.req.param("workspaceId"));
    return c.json(contributorConnectionsResponse.parse({ items }));
  });

  app.post(
    "/workspaces/:workspaceId/contributor/connections/:provider/start",
    ...chain,
    async (c) => {
      const body = contributorConnectStart.safeParse(await c.req.json().catch(() => null));
      const which = provider.safeParse(c.req.param("provider"));
      const returnTo = safeReturnPath(body.data?.return_to);
      if (!returnTo || !which.success)
        return apiFailure(c, 422, "invalid_request", "Invalid request.");
      try {
        const url = await deps.connect.startForContributorUser(
          c.req.param("workspaceId"),
          c.get("user").id,
          returnTo,
          which.data,
        );
        if (!url) return apiFailure(c, 404, "not_found", "Not found.");
        return c.json(authorizationUrlResponse.parse({ authorization_url: url }));
      } catch (error) {
        if (error instanceof ProviderNotConfiguredError)
          return apiFailure(
            c,
            503,
            "reads_not_configured",
            "That provider is not configured here.",
          );
        throw error;
      }
    },
  );

  app.delete(
    "/workspaces/:workspaceId/contributor/connections/:connectionId",
    ...chain,
    async (c) => {
      if (!deps.meStore) return apiFailure(c, 503, "reads_not_configured", "Not configured here.");
      try {
        await deps.meStore.disconnect(caller(c), c.req.param("connectionId"));
        return c.body(null, 204);
      } catch (error) {
        if (error instanceof MeNotFoundError) return apiFailure(c, 404, "not_found", "Not found.");
        throw error;
      }
    },
  );

  return app;
}

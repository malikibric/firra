import type { MiddlewareHandler } from "hono";
import { z } from "zod";
import type { UserTokenVerifier } from "../auth/tokens.js";
import type { ApiRepository } from "../lib/repository.js";
import type { AppEnv } from "../lib/types.js";
import { requireUser, requireWorkspaceRole } from "../middleware/auth.js";
import { rateLimit } from "../middleware/rate-limit.js";
import { apiFailure } from "../middleware/v1-envelope.js";

interface GuardDeps {
  repo: ApiRepository;
  verifyUser: UserTokenVerifier;
  userRateLimitPerMinute: number;
}

/**
 * Guards of every owner/admin route under /v1/workspaces/:workspaceId, in order: the `/v1` error
 * model, user token, UUID path ids, owner/admin role, per-user rate limit. The shared routers
 * answer with their own bodies; the first middleware normalises them.
 */
export function v1Guard(deps: GuardDeps) {
  const { errorModel, pathIds } = shared();
  return [
    errorModel,
    requireUser(deps.verifyUser),
    pathIds,
    requireWorkspaceRole(deps.repo, ["owner", "admin"]),
    rateLimit(deps.userRateLimitPerMinute, Date.now, (c) => c.get("user").id),
  ] as const;
}

/** For routes any signed-in user may call about themselves (contributors included): no workspace role. */
export function v1UserGuard(deps: Pick<GuardDeps, "verifyUser" | "userRateLimitPerMinute">) {
  const { errorModel, pathIds } = shared();
  return [
    errorModel,
    requireUser(deps.verifyUser),
    pathIds,
    rateLimit(deps.userRateLimitPerMinute, Date.now, (c) => c.get("user").id),
  ] as const;
}

function shared() {
  const errorModel: MiddlewareHandler<AppEnv> = async (c, next) => {
    await next();
    // A throwing handler is already logged and sent to Sentry by the app's error handler; here
    // only the body changes, so row data and provider messages never reach the response.
    if (c.error) {
      c.res = apiFailure(c, 500, "internal_error", "The request could not be completed.");
      return;
    }
    const known = {
      401: ["unauthorized", "A valid session is required."],
      403: ["forbidden", "Owner or admin access is required."],
      429: ["rate_limited", "Too many requests."],
    } as const;
    const status = c.res.status;
    if (status === 401 || status === 403 || status === 429) {
      const [code, message] = known[status];
      c.res = apiFailure(c, status, code, message);
    }
  };
  const pathIds: MiddlewareHandler<AppEnv> = async (c, next) => {
    // Only parameters named like ids (`workspaceId`, `clientId`, …) must be UUIDs; `:provider` is not one.
    const ids = Object.entries(c.req.param()).filter(([name]) => name.endsWith("Id"));
    if (!ids.every(([, id]) => z.uuid().safeParse(id).success)) {
      return apiFailure(c, 422, "invalid_request", "Path ids must be UUIDs.");
    }
    await next();
  };
  return { errorModel, pathIds };
}

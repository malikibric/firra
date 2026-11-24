import type { MemberRole } from "@firra/contracts";
import type { MiddlewareHandler } from "hono";
import { z } from "zod";
import type { IdTokenVerifier, UserTokenVerifier } from "../auth/tokens.js";
import type { ApiRepository } from "../lib/repository.js";
import type { AppEnv } from "../lib/types.js";

function bearer(header: string | undefined): string | null {
  if (!header?.startsWith("Bearer ")) return null;
  const token = header.slice("Bearer ".length).trim();
  return token.length > 0 ? token : null;
}

const unauthorized = { error: "unauthorized" } as const;
const forbidden = { error: "forbidden" } as const;

/** Requires a valid Supabase user session token. */
export function requireUser(verify: UserTokenVerifier): MiddlewareHandler<AppEnv> {
  return async (c, next) => {
    const token = bearer(c.req.header("authorization"));
    const user = token ? await verify(token) : null;
    if (!user) return c.json(unauthorized, 401);
    c.set("user", user);
    await next();
  };
}

/** Requires the authenticated user to hold one of `roles` in the :workspaceId route param. */
export function requireWorkspaceRole(
  repo: ApiRepository,
  roles: readonly MemberRole[],
): MiddlewareHandler<AppEnv> {
  return async (c, next) => {
    const workspaceId = z.uuid().safeParse(c.req.param("workspaceId"));
    // Unknown or foreign workspaces are indistinguishable to the caller.
    if (!workspaceId.success) return c.json(forbidden, 403);
    const role = await repo.getMembershipRole(workspaceId.data, c.get("user").id);
    if (!role || !roles.includes(role)) return c.json(forbidden, 403);
    await next();
  };
}

/** Requires a Google ID token from an allowlisted service account (deploy smoke tests, schedulers). */
export function requireInternalCaller(verify: IdTokenVerifier): MiddlewareHandler<AppEnv> {
  return async (c, next) => {
    const token = bearer(c.req.header("authorization"));
    const caller = token ? await verify(token) : null;
    if (!caller) return c.json(unauthorized, 401);
    c.set("internalCaller", caller);
    await next();
  };
}

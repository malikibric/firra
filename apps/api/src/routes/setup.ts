import { setupReadRoutes, setupWriteRoutes } from "@firra/contracts";
import { Hono, type Context } from "hono";
import type { UserTokenVerifier } from "../auth/tokens.js";
import type { ApiRepository } from "../lib/repository.js";
import { SetupNotFoundError, type SetupStore } from "../lib/setup-store.js";
import type { AppEnv } from "../lib/types.js";
import { apiFailure } from "../middleware/v1-envelope.js";
import { restartBackfill } from "../connect/backfill.js";
import type { TaskService } from "../tasks/enqueue.js";
import { auditedRead } from "./read.js";
import { v1Guard, v1UserGuard } from "./v1-guard.js";

interface Deps {
  repo: ApiRepository;
  verifyUser: UserTokenVerifier;
  userRateLimitPerMinute: number;
  setupStore: SetupStore | null;
  tasks: TaskService;
}

/** Thin setup adapters; the caller's existing RPC owns write authorization and auditing. */
export function setupRoutes(deps: Deps): Hono<AppEnv> {
  const app = new Hono<AppEnv>();
  const workspaceGuard = v1Guard(deps);
  const userGuard = v1UserGuard(deps);
  /**
   * A client became Full: mail of that client that was stored as metadata only is read again from
   * the mailboxes of the workspace (12 months, per mailbox), so its text can reach the vaults. The
   * policy change itself already succeeded; a failure here must not undo or hide it.
   */
  async function readMailAgain(c: Context<AppEnv>) {
    const store = deps.setupStore;
    if (!store?.mailConnections) return;
    try {
      const workspaceId = c.req.param("workspaceId") ?? "";
      const caller = {
        userId: c.get("user").id,
        token: c.req.header("authorization")?.slice("Bearer ".length).trim() ?? "",
      };
      const now = new Date();
      // One re-read per mailbox and hour, whichever client asked: toggling policies cannot fan out.
      const nonce = now.toISOString().slice(0, 13);
      for (const connection of await store.mailConnections(caller, workspaceId))
        await restartBackfill(
          deps.tasks,
          workspaceId,
          connection.id,
          now,
          connection.backfill_months,
          nonce,
        );
    } catch {
      // Best effort: the owner can switch the policy again to retry.
    }
  }
  for (const route of setupWriteRoutes) {
    const signedOnly = "signedOnly" in route && route.signedOnly;
    const path = signedOnly ? route.path : `/workspaces/:workspaceId${route.path}`;
    const handler = async (c: Context<AppEnv>) => {
      if (!deps.setupStore)
        return apiFailure(c, 503, "reads_not_configured", "Setup is not configured here.");
      let body: unknown = null;
      if ("request" in route) {
        if (
          c.req.header("content-type")?.split(";")[0]?.trim().toLowerCase() !== "application/json"
        )
          return apiFailure(c, 422, "invalid_request", "Content-Type must be application/json.");
        const parsed = route.request.safeParse(await c.req.json().catch(() => null));
        if (!parsed.success) return apiFailure(c, 422, "invalid_request", "Invalid setup body.");
        body = parsed.data;
      }
      const targetId =
        Object.entries(c.req.param()).find(
          ([key]) => key.endsWith("Id") && key !== "workspaceId",
        )?.[1] ?? null;
      // Only a real change to Full reads mail again; an already-Full client queues nothing.
      let becomesFull = false;
      if (
        route.operation === "setPolicy" &&
        (body as { policy?: string } | null)?.policy === "full" &&
        deps.setupStore.clientPolicy &&
        targetId
      ) {
        const before = await deps.setupStore
          .clientPolicy(
            {
              userId: c.get("user").id,
              token: c.req.header("authorization")?.slice("Bearer ".length).trim() ?? "",
            },
            c.req.param("workspaceId") ?? "",
            targetId,
          )
          .catch(() => "full");
        becomesFull = before !== null && before !== "full";
      }
      try {
        const result = await deps.setupStore.write(
          {
            userId: c.get("user").id,
            token: c.req.header("authorization")?.slice("Bearer ".length).trim() ?? "",
          },
          signedOnly ? null : (c.req.param("workspaceId") ?? ""),
          route.operation,
          targetId,
          body,
        );
        if (becomesFull) await readMailAgain(c);
        if ("response" in route) return c.json(route.response.parse(result));
        return c.body(null, 204);
      } catch (error) {
        if (error instanceof SetupNotFoundError)
          return apiFailure(c, 404, "not_found", "Not found.");
        throw error;
      }
    };
    if (signedOnly) app.on(route.method, path, ...userGuard, handler);
    else app.on(route.method, path, ...workspaceGuard, handler);
  }
  for (const route of setupReadRoutes) {
    auditedRead(
      app,
      deps,
      workspaceGuard,
      route.path,
      `read.setup_${route.operation}`,
      async ({ c, caller, workspaceId }) => {
        if (!deps.setupStore)
          return apiFailure(c, 503, "reads_not_configured", "Setup is not configured here.");
        const rawPage = c.req.query("page");
        // Same bounded 50-row pagination as web audit, but invalid API parameters are explicit 422s.
        if (
          route.operation === "audit" &&
          rawPage !== undefined &&
          (!/^[1-9]\d{0,2}$/.test(rawPage) || Number(rawPage) > 200)
        )
          return apiFailure(c, 422, "invalid_request", "Invalid audit page.");
        try {
          const body = route.response.parse(
            await deps.setupStore.read(
              caller,
              workspaceId,
              route.operation,
              c.req.param("clientId"),
              Number(rawPage ?? 1),
            ),
          );
          return {
            body,
            count:
              "items" in body ? body.items.length : "domains" in body ? body.domains.length : 1,
          };
        } catch (error) {
          if (error instanceof SetupNotFoundError) return null;
          throw error;
        }
      },
    );
  }
  return app;
}

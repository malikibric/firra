import { randomUUID } from "node:crypto";
import { type Context, Hono } from "hono";
import { z } from "zod";
import type { IdTokenVerifier, UserTokenVerifier } from "../auth/tokens.js";
import type { ApiRepository, JobRecord } from "../lib/repository.js";
import type { AppEnv } from "../lib/types.js";
import { requireInternalCaller, requireUser, requireWorkspaceRole } from "../middleware/auth.js";
import type { TaskService } from "../tasks/enqueue.js";

interface Deps {
  repo: ApiRepository;
  tasks: TaskService;
  verifyUser: UserTokenVerifier;
  verifyInternal: IdTokenVerifier;
}

function jobView(job: JobRecord) {
  return {
    id: job.id,
    type: job.type,
    status: job.status,
    attempts: job.attempts,
    progress: job.progress,
    error_class: job.errorClass,
    created_at: job.createdAt,
    finished_at: job.finishedAt,
  };
}

const idempotencyKey = z
  .string()
  .min(1)
  .max(100)
  .regex(/^[\w.:-]+$/);

function pingKey(header: string | undefined): string | null {
  if (header === undefined) return `diagnostics.ping:${randomUUID()}`;
  const parsed = idempotencyKey.safeParse(header);
  return parsed.success ? `diagnostics.ping:${parsed.data}` : null;
}

/** User-facing diagnostics: owners/admins can trigger a round-trip ping and read job status. */
export function userDiagnosticsRoutes(deps: Deps): Hono<AppEnv> {
  const app = new Hono<AppEnv>();

  app.post(
    "/workspaces/:workspaceId/diagnostics/ping",
    requireUser(deps.verifyUser),
    requireWorkspaceRole(deps.repo, ["owner", "admin"]),
    async (c) => {
      const key = pingKey(c.req.header("idempotency-key"));
      if (!key) return c.json({ error: "invalid_idempotency_key" }, 400);
      const job = await deps.tasks.enqueue({
        workspaceId: c.req.param("workspaceId"),
        type: "diagnostics.ping",
        idempotencyKey: key,
        payload: { requested_at: new Date().toISOString() },
      });
      return c.json(jobView(job), 202);
    },
  );

  app.get(
    "/workspaces/:workspaceId/jobs/:jobId",
    requireUser(deps.verifyUser),
    requireWorkspaceRole(deps.repo, ["owner", "admin"]),
    async (c) => {
      const jobId = z.uuid().safeParse(c.req.param("jobId"));
      if (!jobId.success) return c.json({ error: "not_found" }, 404);
      const job = await deps.repo.getJob(c.req.param("workspaceId"), jobId.data);
      return job ? c.json(jobView(job)) : c.json({ error: "not_found" }, 404);
    },
  );

  return app;
}

/**
 * Internal diagnostics for the post-deploy smoke test. Named maintenance endpoint: callers are
 * allowlisted service accounts (Google ID token), and each call targets one explicit workspace.
 */
export function internalDiagnosticsRoutes(deps: Deps): Hono<AppEnv> {
  const app = new Hono<AppEnv>();
  app.use("*", requireInternalCaller(deps.verifyInternal));

  const internalTask =
    (type: "diagnostics.ping" | "diagnostics.llm_probe") => async (c: Context<AppEnv>) => {
      const body = z
        .object({ workspace_id: z.uuid() })
        .safeParse(await c.req.json().catch(() => null));
      if (!body.success) return c.json({ error: "invalid_request" }, 400);
      if (!(await deps.repo.workspaceExists(body.data.workspace_id)))
        return c.json({ error: "not_found" }, 404);
      const job = await deps.tasks.enqueue({
        workspaceId: body.data.workspace_id,
        type,
        idempotencyKey: `${type}:${randomUUID()}`,
        payload: { requested_at: new Date().toISOString() },
      });
      c.get("logger").info(`internal.${type.replace(".", "_")}`, {
        workspace_id: body.data.workspace_id,
        job_id: job.id,
      });
      return c.json(jobView(job), 202);
    };

  app.post("/diagnostics/ping", internalTask("diagnostics.ping"));
  // Calls each configured Gemini model once on the EU endpoint (the design notes).
  app.post("/diagnostics/llm-probe", internalTask("diagnostics.llm_probe"));

  app.get("/workspaces/:workspaceId/jobs/:jobId", async (c) => {
    const ids = z.object({ workspaceId: z.uuid(), jobId: z.uuid() }).safeParse(c.req.param());
    if (!ids.success) return c.json({ error: "not_found" }, 404);
    const job = await deps.repo.getJob(ids.data.workspaceId, ids.data.jobId);
    return job ? c.json(jobView(job)) : c.json({ error: "not_found" }, 404);
  });

  return app;
}

import { randomUUID } from "node:crypto";
import type { Database } from "@firra/db-types";
import type { SupabaseClient } from "@supabase/supabase-js";
import { Hono } from "hono";
import { cors } from "hono/cors";
import { z } from "zod";
import type { UserTokenVerifier } from "../auth/tokens.js";
import type { ApiRepository } from "../lib/repository.js";
import type { AppEnv } from "../lib/types.js";
import { requireUser, requireWorkspaceRole } from "../middleware/auth.js";
import type { TaskService } from "../tasks/enqueue.js";
import type { LocalUploadStorage, UploadStorage } from "../uploads/storage.js";

/** Largest accepted export; the worker parses one file per task. */
export const MAX_UPLOAD_BYTES = 200 * 1024 * 1024;

export interface UploadConnectionStore {
  /** The signed-in member's own upload connection in this workspace (created on first use). */
  ensureUploadConnection(workspaceId: string, userId: string, label: string): Promise<string>;
  isUploadConnection(workspaceId: string, connectionId: string): Promise<boolean>;
}

export class SupabaseUploadConnectionStore implements UploadConnectionStore {
  constructor(private readonly db: SupabaseClient<Database>) {}

  async ensureUploadConnection(workspaceId: string, userId: string, label: string) {
    const externalId = `upload:${userId}`;
    const find = () =>
      this.db
        .from("connection")
        .select("id")
        .eq("workspace_id", workspaceId)
        .eq("provider", "email_upload")
        .eq("mode", "upload")
        .eq("external_account_id", externalId)
        .maybeSingle();
    const { data: existing, error } = await find();
    if (error) throw new Error(`upload connection lookup failed: ${error.code}`);
    if (existing) {
      await this.db
        .from("connection")
        .update({ status: "active" })
        .eq("workspace_id", workspaceId)
        .eq("id", existing.id);
      return existing.id;
    }
    const { data, error: insertError } = await this.db
      .from("connection")
      .insert({
        workspace_id: workspaceId,
        provider: "email_upload",
        mode: "upload",
        owner_user_id: userId,
        account_label: label,
        external_account_id: externalId,
        backfill_months: 0,
      })
      .select("id")
      .single();
    if (insertError) {
      // A concurrent first upload created it: use that row.
      const { data: raced } = await find();
      if (raced) return raced.id;
      throw new Error(`upload connection insert failed: ${insertError.code}`);
    }
    return data.id;
  }

  async isUploadConnection(workspaceId: string, connectionId: string) {
    const { data, error } = await this.db
      .from("connection")
      .select("id")
      .eq("workspace_id", workspaceId)
      .eq("id", connectionId)
      .eq("mode", "upload")
      .neq("status", "revoked")
      .maybeSingle();
    if (error) throw new Error(`upload connection lookup failed: ${error.code}`);
    return data !== null;
  }
}

interface Deps {
  repo: ApiRepository;
  verifyUser: UserTokenVerifier;
  tasks: TaskService;
  uploads: UploadStorage | null;
  uploadConnections: UploadConnectionStore;
}

const format = z.enum(["mbox", "eml"]);
const startBody = z.object({
  size: z.number().int().positive().max(MAX_UPLOAD_BYTES),
  format,
});
const UPLOAD_OBJECT = /^uploads\/([0-9a-f-]{36})\/([0-9a-f-]{36})\/[0-9a-f-]{36}\.(mbox|eml)$/;
const completeBody = z.object({ object: z.string().max(200), format });

/**
 * POST /workspaces/:id/uploads            → { upload_url, object } (owner/admin)
 * POST /workspaces/:id/uploads/complete   → queues ingest.upload for an uploaded object
 */
export function uploadRoutes(deps: Deps): Hono<AppEnv> {
  const app = new Hono<AppEnv>();
  const { uploads } = deps;
  for (const path of ["/workspaces/:workspaceId/uploads", "/workspaces/:workspaceId/uploads/*"])
    app.use(
      path,
      requireUser(deps.verifyUser),
      requireWorkspaceRole(deps.repo, ["owner", "admin"]),
    );
  if (!uploads) {
    app.post("/workspaces/:workspaceId/uploads/*", (c) =>
      c.json({ error: "uploads_not_configured" }, 503),
    );
    app.post("/workspaces/:workspaceId/uploads", (c) =>
      c.json({ error: "uploads_not_configured" }, 503),
    );
    return app;
  }

  app.post("/workspaces/:workspaceId/uploads", async (c) => {
    const body = startBody.safeParse(await c.req.json().catch(() => null));
    if (!body.success) return c.json({ error: "invalid_request" }, 400);
    const workspaceId = c.req.param("workspaceId");
    const user = c.get("user");
    const connectionId = await deps.uploadConnections.ensureUploadConnection(
      workspaceId,
      user.id,
      user.email ?? "upload",
    );
    // Object names are ids only: the original filename is never stored.
    const object = `uploads/${workspaceId}/${connectionId}/${randomUUID()}.${body.data.format}`;
    const uploadUrl = await uploads.createUpload(object, body.data.size);
    return c.json({ upload_url: uploadUrl, object });
  });

  app.post("/workspaces/:workspaceId/uploads/complete", async (c) => {
    const body = completeBody.safeParse(await c.req.json().catch(() => null));
    const workspaceId = c.req.param("workspaceId");
    const match = UPLOAD_OBJECT.exec(body.data?.object ?? "");
    if (!body.success || match?.[1] !== workspaceId || !match[2] || match[3] !== body.data.format)
      return c.json({ error: "invalid_request" }, 400);
    const connectionId = match[2];
    if (!(await deps.uploadConnections.isUploadConnection(workspaceId, connectionId)))
      return c.json({ error: "invalid_request" }, 400);
    const size = await uploads.objectSize(body.data.object);
    if (size === null) return c.json({ error: "not_uploaded" }, 409);
    if (size > MAX_UPLOAD_BYTES) return c.json({ error: "too_large" }, 413);

    const job = await deps.tasks.enqueue({
      workspaceId,
      connectionId,
      type: "ingest.upload",
      idempotencyKey: `upload:${body.data.object}`,
      payload: {
        connection_id: connectionId,
        object_uri: `gs://${uploads.bucket}/${body.data.object}`,
        format: body.data.format,
      },
    });
    return c.json({ job_id: job.id }, 202);
  });

  return app;
}

/** PUT /local-uploads/:token — local/test only, stands in for the GCS session URL. */
export function localUploadRoutes(
  storage: LocalUploadStorage,
  webUrl: string | undefined,
): Hono<AppEnv> {
  const app = new Hono<AppEnv>();
  if (webUrl) app.use("/local-uploads/*", cors({ origin: webUrl, allowMethods: ["PUT"] }));
  app.put("/local-uploads/:token", async (c) => {
    const declared = Number(c.req.header("content-length") ?? "0");
    if (declared > MAX_UPLOAD_BYTES) return c.json({ error: "too_large" }, 413);
    const body = new Uint8Array(await c.req.arrayBuffer());
    const stored = await storage.receive(c.req.param("token"), body);
    return stored ? c.body(null, 200) : c.json({ error: "invalid_upload" }, 400);
  });
  return app;
}

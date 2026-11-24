/**
 * Push notifications that wake a connection's incremental sync. They carry no mail content: the
 * worker always reads changes through the provider API.
 *
 * - Gmail: Gmail publishes {emailAddress, historyId} to Pub/Sub, which pushes it here with a
 *   Google-signed OIDC token (audience `<api>/webhooks/gmail`, our push service account).
 * - Microsoft Graph: notification URLs carry `w` (workspace) and `c` (connection); Graph first
 *   validates the URL (echo `validationToken` as text/plain within 10 s), then posts
 *   notifications whose `clientState` must match the sha256 the worker stored for that
 *   connection. Lifecycle notifications ask the worker to renew the subscription.
 */
import { createHash, timingSafeEqual } from "node:crypto";
import type { Database } from "@firra/db-types";
import type { SupabaseClient } from "@supabase/supabase-js";
import { Hono } from "hono";
import { z } from "zod";
import type { IdTokenVerifier } from "../auth/tokens.js";
import type { AppEnv } from "../lib/types.js";
import type { TaskService } from "../tasks/enqueue.js";
import type { WebhookProvider, WebhookRequest } from "./verifier.js";

export interface PushConnection {
  status: string;
  clientStateSha256: string | null;
}

export interface PushStore {
  /**
   * Routing for Gmail notifications, which name only the mailbox: the one cross-workspace lookup
   * in apps/api. Returns ids of active Gmail connections for that address, nothing else.
   */
  gmailConnectionsForAddress(
    email: string,
  ): Promise<{ workspaceId: string; connectionId: string }[]>;
  pushConnection(workspaceId: string, connectionId: string): Promise<PushConnection | null>;
}

export class SupabasePushStore implements PushStore {
  constructor(private readonly db: SupabaseClient<Database>) {}

  async gmailConnectionsForAddress(email: string) {
    const { data, error } = await this.db
      .from("connection")
      .select("id, workspace_id")
      .eq("provider", "gmail")
      .eq("status", "active")
      .eq("account_label", email)
      .limit(50);
    if (error) throw new Error(`gmail push routing failed: ${error.code}`);
    return data.map((row) => ({ workspaceId: row.workspace_id, connectionId: row.id }));
  }

  async pushConnection(workspaceId: string, connectionId: string) {
    const { data, error } = await this.db
      .from("connection")
      .select("status, cursor")
      .eq("workspace_id", workspaceId)
      .eq("id", connectionId)
      .maybeSingle();
    if (error) throw new Error(`push connection lookup failed: ${error.code}`);
    if (!data) return null;
    const push = z
      .object({ push: z.object({ client_state_sha256: z.string() }).partial() })
      .partial()
      .safeParse(data.cursor);
    return {
      status: data.status,
      clientStateSha256: push.success ? (push.data.push?.client_state_sha256 ?? null) : null,
    };
  }
}

interface PushDeps {
  tasks: TaskService;
  store: PushStore;
  now?: () => number;
}

/** One sync per connection and minute at most; the 15-minute sweep covers anything later. */
async function wake(
  deps: PushDeps,
  workspaceId: string,
  connectionId: string,
  renew = false,
): Promise<void> {
  const minute = Math.floor((deps.now ?? Date.now)() / 60_000);
  await deps.tasks.enqueue({
    workspaceId,
    connectionId,
    type: "sync.incremental",
    idempotencyKey: `${renew ? "push-renew" : "push"}:${connectionId}:${String(minute)}`,
    payload: { connection_id: connectionId, renew_push: renew },
  });
}

const pubsubPush = z.object({
  message: z.object({ data: z.string().max(10_000) }),
});
const gmailNotification = z.object({ emailAddress: z.email(), historyId: z.unknown() });

export function gmailPushProvider(
  deps: PushDeps & { verifyIdToken: IdTokenVerifier },
): WebhookProvider {
  return {
    name: "gmail",
    verifier: {
      async verify({ headers }: WebhookRequest) {
        const token = /^Bearer (.+)$/.exec(headers.get("authorization") ?? "")?.[1];
        return token ? (await deps.verifyIdToken(token)) !== null : false;
      },
    },
    async handle({ rawBody }: WebhookRequest) {
      // Malformed messages are acknowledged (not retried): Pub/Sub would redeliver them forever.
      let parsed: unknown;
      try {
        const envelope = pubsubPush.parse(JSON.parse(rawBody));
        parsed = JSON.parse(Buffer.from(envelope.message.data, "base64").toString("utf8"));
      } catch {
        return;
      }
      const notification = gmailNotification.safeParse(parsed);
      if (!notification.success) return;
      const email = notification.data.emailAddress.toLowerCase();
      for (const target of await deps.store.gmailConnectionsForAddress(email)) {
        await wake(deps, target.workspaceId, target.connectionId);
      }
    },
  };
}

const uuid = z.uuid();
const graphNotifications = z.object({
  value: z
    .array(
      z.object({
        clientState: z.string().max(128).optional(),
        lifecycleEvent: z.string().optional(),
      }),
    )
    .max(1000),
});
const RENEW_EVENTS = new Set(["reauthorizationRequired", "subscriptionRemoved", "missed"]);

const sha256 = (value: string) => createHash("sha256").update(value).digest();

/** POST /webhooks/microsoft?w=&c= (Graph change and lifecycle notifications). */
export function microsoftPushRoutes(deps: PushDeps): Hono<AppEnv> {
  const app = new Hono<AppEnv>();
  app.post("/microsoft", async (c) => {
    const validationToken = c.req.query("validationToken");
    if (validationToken !== undefined) {
      // Graph checks the endpoint by asking for its opaque token back, as plain text.
      if (validationToken.length === 0 || validationToken.length > 1024)
        return c.json({ error: "invalid_request" }, 400);
      return c.text(validationToken, 200, { "Content-Type": "text/plain; charset=utf-8" });
    }

    const workspaceId = uuid.safeParse(c.req.query("w"));
    const connectionId = uuid.safeParse(c.req.query("c"));
    const body = graphNotifications.safeParse(await c.req.json().catch(() => null));
    if (!workspaceId.success || !connectionId.success || !body.success)
      return c.json({ error: "invalid_request" }, 400);

    const connection = await deps.store.pushConnection(workspaceId.data, connectionId.data);
    const expected = connection?.clientStateSha256
      ? Buffer.from(connection.clientStateSha256, "hex")
      : null;
    const valid = body.data.value.filter((item) => {
      if (!expected || item.clientState === undefined) return false;
      const received = sha256(item.clientState);
      return received.length === expected.length && timingSafeEqual(received, expected);
    });
    if (valid.length === 0) {
      c.get("logger").warn("webhook.rejected", { provider: "microsoft" });
      return c.json({ error: "invalid_client_state" }, 401);
    }
    if (connection?.status === "active") {
      const renew = valid.some((item) => RENEW_EVENTS.has(item.lifecycleEvent ?? ""));
      await wake(deps, workspaceId.data, connectionId.data, renew);
    }
    return c.body(null, 202);
  });
  return app;
}

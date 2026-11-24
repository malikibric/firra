import {
  vaultKeepRequest,
  vaultKeepResponse,
  vaultAckRequest,
  vaultAckResponse,
  vaultRecordsResponse,
  vaultRefetchResponse,
} from "@firra/contracts";
import { Hono, type Context } from "hono";
import type { UserTokenVerifier } from "../auth/tokens.js";
import { clientIp } from "../lib/client-ip.js";
import type { ApiRepository } from "../lib/repository.js";
import type { AppEnv } from "../lib/types.js";
import {
  parseVaultPage,
  vaultPage,
  type VaultStore,
  type VaultTarget,
} from "../lib/vault-store.js";
import type { TaskService } from "../tasks/enqueue.js";
import { apiFailure } from "../middleware/v1-envelope.js";
import { v1UserGuard } from "./v1-guard.js";

interface Deps {
  repo: ApiRepository;
  verifyUser: UserTokenVerifier;
  userRateLimitPerMinute: number;
  vaultStore: VaultStore | null;
  tasks: TaskService;
}
const INET = /^[0-9a-fA-F:.]{2,45}$/;

/** Bodies leave only for their mailbox owner; audit succeeds before any response or ack write. */
export function vaultRoutes(deps: Deps): Hono<AppEnv> {
  const app = new Hono<AppEnv>();
  const chain = v1UserGuard(deps);
  async function audit(
    c: Context<AppEnv>,
    store: VaultStore,
    action: string,
    rows: VaultTarget[],
    keptCount?: number,
  ) {
    const counts = new Map<string, number>();
    for (const row of rows) counts.set(row.workspace_id, (counts.get(row.workspace_id) ?? 0) + 1);
    if (!counts.size) {
      for (const id of await store.auditWorkspaces(c.get("user").id))
        counts.set(id, keptCount ?? 0);
    }
    // audit_log requires a real workspace. A user with no tenant/owned records fails closed.
    if (!counts.size) throw new Error("vault audit workspace unavailable");
    const ip = clientIp(c.req.header("x-forwarded-for"));
    for (const [workspaceId, count] of counts) {
      await deps.repo.writeAudit({
        workspaceId,
        actorId: c.get("user").id,
        action,
        ip: INET.test(ip) ? ip : null,
        userAgent: c.req.header("user-agent") ?? null,
        metadata: { count },
      });
    }
  }
  app.post("/vault/keep", ...chain, async (c) => {
    const store = deps.vaultStore;
    if (!store) return apiFailure(c, 503, "reads_not_configured", "Vault is not configured here.");
    if (c.req.header("content-type")?.split(";")[0]?.trim().toLowerCase() !== "application/json")
      return apiFailure(c, 422, "invalid_request", "Content-Type must be application/json.");
    const request = vaultKeepRequest.safeParse(await c.req.json().catch(() => null));
    if (!request.success) return apiFailure(c, 422, "invalid_request", "Invalid client ids.");
    const body = vaultKeepResponse.parse({
      keep: await store.keepable(c.get("user").id, [...new Set(request.data.client_ids)]),
    });
    await audit(c, store, "read.vault_keep", [], body.keep.length);
    return c.json(body);
  });
  app.get("/vault/records", ...chain, async (c) => {
    const store = deps.vaultStore;
    if (!store) return apiFailure(c, 503, "reads_not_configured", "Vault is not configured here.");
    const query = parseVaultPage(c.req.query("limit"), c.req.query("cursor"));
    if (!query) return apiFailure(c, 422, "invalid_request", "Invalid pagination parameters.");
    const body = vaultRecordsResponse.parse(
      vaultPage(await store.records(c.get("user").id, query), query),
    );
    await audit(c, store, "read.vault_records", body.items);
    return c.json(body);
  });
  app.post("/vault/ack", ...chain, async (c) => {
    const store = deps.vaultStore;
    if (!store) return apiFailure(c, 503, "reads_not_configured", "Vault is not configured here.");
    if (c.req.header("content-type")?.split(";")[0]?.trim().toLowerCase() !== "application/json")
      return apiFailure(c, 422, "invalid_request", "Content-Type must be application/json.");
    const request = vaultAckRequest.safeParse(await c.req.json().catch(() => null));
    if (!request.success)
      return apiFailure(c, 422, "invalid_request", "Invalid acknowledgement ids.");
    const userId = c.get("user").id;
    const targets = await store.pendingAck(userId, [...new Set(request.data.ids)]);
    await audit(c, store, "vault.acked", targets);
    return c.json(
      vaultAckResponse.parse({
        acknowledged: await store.ack(
          userId,
          targets.map((row) => row.id),
        ),
      }),
    );
  });
  app.post("/vault/refetch", ...chain, async (c) => {
    const store = deps.vaultStore;
    if (!store) return apiFailure(c, 503, "reads_not_configured", "Vault is not configured here.");
    const userId = c.get("user").id;
    const mailboxes = await store.ownMailboxes(userId);
    await audit(
      c,
      store,
      "vault.refetch_requested",
      mailboxes.map(({ id, workspace_id }) => ({ id, workspace_id })),
    );
    const day = new Date().toISOString().slice(0, 10);
    // One run per mailbox per day: asking twice the same day returns the same job.
    for (const mailbox of mailboxes) {
      await deps.tasks.enqueue({
        workspaceId: mailbox.workspace_id,
        connectionId: mailbox.id,
        type: "vault.rehydrate",
        idempotencyKey: `vault-rehydrate:${mailbox.id}:${userId}:${day}`,
        payload: { connection_id: mailbox.id, user_id: userId },
      });
    }
    return c.json(vaultRefetchResponse.parse({ requested: mailboxes.length }));
  });
  return app;
}

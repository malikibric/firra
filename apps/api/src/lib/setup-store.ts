import {
  acceptInviteRequest,
  createWorkspaceRequest,
  createClientRequest,
  updateClientRequest,
  clientDomainsRequest,
  clientPolicyRequest,
  saveServiceRequest,
  inviteAdminRequest,
  inviteContributorRequest,
  requestReplayRequest,
  type SetupRead,
  type SetupWrite,
} from "@firra/contracts";
import type { Database } from "@firra/db-types";
import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import type { ReadCaller } from "./read-store.js";
import type { InviteEmailSender } from "./invite-email.js";

export class SetupNotFoundError extends Error {}
export interface SetupStore {
  read(
    caller: ReadCaller,
    workspaceId: string,
    operation: SetupRead,
    targetId?: string,
    page?: number,
  ): Promise<unknown>;
  write(
    caller: ReadCaller,
    workspaceId: string | null,
    operation: SetupWrite,
    targetId: string | null,
    body: unknown,
  ): Promise<unknown>;
  /** A client's current policy as the caller sees it (null when not visible). */
  clientPolicy?(caller: ReadCaller, workspaceId: string, clientId: string): Promise<string | null>;
  /** The workspace's active Gmail/Outlook mailboxes (also org-wide ones), to read their mail again. */
  mailConnections?(
    caller: ReadCaller,
    workspaceId: string,
  ): Promise<{ id: string; backfill_months: number }[]>;
}

type TargetTable =
  "client" | "workspace_service" | "workspace_invite" | "contributor" | "connection";
function checked<T>(result: { data: T; error: { code?: string } | null }): T {
  if (result.error?.code === "42501") throw new SetupNotFoundError();
  if (result.error) throw new Error("setup operation failed");
  return result.data;
}

function rows<T>(result: { data: T[] | null; error: { code?: string } | null }): T[] {
  const data = checked(result);
  if (!data) throw new Error("setup read failed");
  return data;
}

/** All writes run through existing audited RPCs as the caller. Reads use explicit safe columns. */
export class SupabaseSetupStore implements SetupStore {
  constructor(
    private readonly url: string,
    private readonly publishableKey: string,
    private readonly sendInviteEmail: InviteEmailSender = () => Promise.resolve(false),
  ) {}

  async mailConnections(caller: ReadCaller, workspaceId: string) {
    const { data, error } = await this.asCaller(caller)
      .from("connection")
      .select("id, backfill_months")
      .eq("workspace_id", workspaceId)
      .eq("status", "active")
      .in("provider", ["gmail", "outlook"]);
    if (error) throw new Error("setup connection lookup failed");
    return data;
  }

  async clientPolicy(caller: ReadCaller, workspaceId: string, clientId: string) {
    const { data, error } = await this.asCaller(caller)
      .from("client")
      .select("policy")
      .eq("workspace_id", workspaceId)
      .eq("id", clientId)
      .maybeSingle();
    if (error) throw new Error("setup client lookup failed");
    return data?.policy ?? null;
  }

  private asCaller(caller: ReadCaller) {
    return createClient<Database>(this.url, this.publishableKey, {
      auth: { persistSession: false, autoRefreshToken: false },
      global: { headers: { Authorization: `Bearer ${caller.token}` } },
    });
  }
  private async visible(
    db: SupabaseClient<Database>,
    workspaceId: string,
    table: TargetTable,
    id: string,
  ) {
    const row = checked(
      await db.from(table).select("id").eq("workspace_id", workspaceId).eq("id", id).maybeSingle(),
    );
    if (!row) throw new SetupNotFoundError();
  }
  async write(
    caller: ReadCaller,
    workspaceId: string | null,
    operation: SetupWrite,
    targetId: string | null,
    body: unknown,
  ): Promise<unknown> {
    const db = this.asCaller(caller);
    const ws = workspaceId ?? "";
    const id = targetId ?? "";
    const targets: Partial<Record<SetupWrite, TargetTable>> = {
      updateClient: "client",
      setDomains: "client",
      setPolicy: "client",
      saveService: "workspace_service",
      deleteService: "workspace_service",
      revokeInvite: "workspace_invite",
      revokeContributor: "contributor",
      disconnect: "connection",
    };
    const table = targets[operation];
    // RPCs taking only a target id must also honor the workspace named by the URL. Even a
    // caller who owns two workspaces cannot mutate B through A's path.
    if (table) await this.visible(db, ws, table, id);
    switch (operation) {
      case "createWorkspace": {
        const input = createWorkspaceRequest.parse(body);
        const value = checked(
          await db.rpc("create_workspace", {
            p_name: input.name,
            p_timezone: input.timezone,
            p_default_locale: input.default_locale,
          }),
        );
        return { id: value };
      }
      case "acceptInvite":
        return {
          id: checked(
            await db.rpc("accept_workspace_invite", {
              p_token: acceptInviteRequest.parse(body).token,
            }),
          ),
        };
      case "createClient": {
        const input = createClientRequest.parse(body);
        return {
          id: checked(
            await db.rpc("create_client", {
              p_workspace_id: ws,
              p_name: input.name,
              p_policy: input.policy,
              p_domains: input.domains,
            }),
          ),
        };
      }
      case "updateClient": {
        const input = updateClientRequest.parse(body);
        checked(
          await db.rpc("update_client", {
            p_client_id: id,
            p_name: input.name,
            p_status: input.status,
          }),
        );
        break;
      }
      case "setDomains":
        checked(
          await db.rpc("set_client_domains", {
            p_client_id: id,
            p_domains: clientDomainsRequest.parse(body).domains,
          }),
        );
        break;
      case "setPolicy":
        checked(
          await db.rpc("set_client_policy", {
            p_client_id: id,
            p_policy: clientPolicyRequest.parse(body).policy,
          }),
        );
        break;
      case "createService":
      case "saveService": {
        const input = saveServiceRequest.parse(body);
        const keywords = [...new Set(input.keywords.map((keyword) => keyword.toLowerCase()))];
        return {
          id: checked(
            await db.rpc("save_workspace_service", {
              p_workspace_id: ws,
              p_service_id: operation === "createService" ? (null as unknown as string) : id,
              p_name: input.name,
              p_description: input.description,
              p_keywords: keywords,
            }),
          ),
        };
      }
      case "deleteService":
        checked(await db.rpc("delete_workspace_service", { p_service_id: id }));
        break;
      case "inviteAdmin": {
        const input = inviteAdminRequest.parse(body);
        const row = checked(
          await db.rpc("create_workspace_invite", {
            p_workspace_id: ws,
            p_email: input.email.toLowerCase(),
          }),
        )?.[0];
        if (!row) throw new Error("setup invite failed");
        const { data: workspace } = await db.from("workspace").select("name").eq("id", ws).single();
        // Sending is best effort; the one-time code remains usable when email is unconfigured.
        const emailed = await this.sendInviteEmail({
          email: input.email.toLowerCase(),
          workspace: workspace?.name ?? "Firra",
          token: row.token,
        }).catch(() => false);
        return { id: row.invite_id, token: row.token, expires_at: row.expires_at, emailed };
      }
      case "revokeInvite":
        checked(await db.rpc("revoke_workspace_invite", { p_invite_id: id }));
        break;
      case "removeMember":
        checked(await db.rpc("remove_member", { p_workspace_id: ws, p_user_id: id }));
        break;
      case "inviteContributor": {
        const input = inviteContributorRequest.parse(body);
        const row = checked(
          await db.rpc("create_contributor_invite", {
            p_workspace_id: ws,
            p_email: input.email.toLowerCase(),
            ...(input.name ? { p_name: input.name } : {}),
          }),
        )?.[0];
        if (!row) throw new Error("setup invite failed");
        const { data: workspace } = await db.from("workspace").select("name").eq("id", ws).single();
        await this.sendInviteEmail({
          email: input.email.toLowerCase(),
          workspace: workspace?.name ?? "Firra",
          token: row.token,
        }).catch(() => false);
        return { id: row.contributor_id, token: row.token };
      }
      case "revokeContributor":
        checked(await db.rpc("revoke_contributor", { p_contributor_id: id }));
        break;
      case "disconnect":
        checked(await db.rpc("disconnect_connection", { p_connection_id: id }));
        break;
      case "requestReplay": {
        const input = requestReplayRequest.parse(body);
        return {
          id: checked(
            await db.rpc("request_replay_run", {
              p_workspace_id: ws,
              p_window_start: input.window_start,
              p_window_end: input.window_end,
              p_step_days: input.step_days,
            }),
          ),
        };
      }
    }
    return null;
  }

  async read(
    caller: ReadCaller,
    workspaceId: string,
    operation: SetupRead,
    targetId?: string,
    page = 1,
  ): Promise<unknown> {
    const db = this.asCaller(caller);
    const ws = workspaceId;
    switch (operation) {
      case "services":
        return {
          items: rows(
            await db
              .from("workspace_service")
              .select("id, name, description, keywords, created_at")
              .eq("workspace_id", ws)
              .order("name"),
          ),
        };
      case "members":
        return { items: rows(await db.rpc("list_workspace_members", { p_workspace_id: ws })) };
      case "contributors":
        return {
          items: rows(
            await db
              .from("contributor")
              .select("id, email, name, status, created_at")
              .eq("workspace_id", ws)
              .order("created_at", { ascending: false }),
          ),
        };
      case "invites":
        return {
          items: rows(
            await db
              .from("workspace_invite")
              .select("id, email, expires_at, created_at")
              .eq("workspace_id", ws)
              .is("accepted_at", null)
              .is("revoked_at", null)
              .gt("expires_at", new Date().toISOString())
              .order("created_at", { ascending: false }),
          ),
        };
      case "audit": {
        const entries = rows(
          await db
            .from("audit_log")
            .select("id, created_at, action, actor_type, actor_id, target_type, target_id")
            .eq("workspace_id", ws)
            .order("created_at", { ascending: false })
            .order("id", { ascending: false })
            .range((page - 1) * 50, page * 50),
        );
        return {
          items: entries.slice(0, 50),
          next_page: entries.length > 50 && page < 200 ? page + 1 : null,
        };
      }
      case "domains": {
        await this.visible(db, ws, "client", targetId ?? "");
        return {
          domains: rows(
            await db
              .from("client_domain")
              .select("domain")
              .eq("workspace_id", ws)
              .eq("client_id", targetId ?? "")
              .order("domain"),
          ).map((row) => row.domain),
        };
      }
      case "clients": {
        const [clients, domains] = await Promise.all([
          db
            .from("client")
            .select("id, name, status, policy, created_at")
            .eq("workspace_id", ws)
            .order("name"),
          db
            .from("client_domain")
            .select("client_id, domain")
            .eq("workspace_id", ws)
            .order("domain"),
        ]);
        const mappings = rows(domains);
        return {
          items: rows(clients).map((client) => ({
            ...client,
            domains: mappings.filter((row) => row.client_id === client.id).map((row) => row.domain),
          })),
        };
      }
      case "connections": {
        const [connections, jobs, contributors] = await Promise.all([
          db
            .from("connection")
            .select(
              "id, provider, mode, status, account_label, owner_user_id, owner_contributor_id, last_synced_at, last_error_class, created_at",
            )
            .eq("workspace_id", ws)
            .order("created_at", { ascending: false }),
          db
            .from("job")
            .select("connection_id, status")
            .eq("workspace_id", ws)
            .in("type", ["sync.backfill_chunk", "ingest.upload"]),
          db.from("contributor").select("id, email").eq("workspace_id", ws),
        ]);
        const imports = rows(jobs);
        const owners = rows(contributors);
        return {
          items: rows(connections).map((row) => {
            const progress = imports.filter((job) => job.connection_id === row.id);
            return {
              ...row,
              contributor_email:
                owners.find((owner) => owner.id === row.owner_contributor_id)?.email ?? null,
              imports: {
                total: progress.length,
                done: progress.filter((job) => job.status === "succeeded").length,
                failed: progress.filter((job) => job.status === "failed").length,
              },
            };
          }),
        };
      }
      case "setup": {
        const [workspace, clients, domains, connections, jobs, budget] = await Promise.all([
          db.from("workspace").select("id, name, timezone, default_locale").eq("id", ws).single(),
          db.from("client").select("id, status, policy").eq("workspace_id", ws),
          db.from("client_domain").select("client_id").eq("workspace_id", ws),
          db.from("connection").select("id, status").eq("workspace_id", ws),
          db
            .from("job")
            .select("connection_id, status")
            .eq("workspace_id", ws)
            .in("type", ["sync.backfill_chunk", "ingest.upload"]),
          db
            .from("llm_budget_event")
            .select("threshold")
            .eq("workspace_id", ws)
            .eq("month", new Date().toISOString().slice(0, 7) + "-01")
            .order("threshold", { ascending: false })
            .limit(1),
        ]);
        const activeConnections = new Set(
          rows(connections)
            .filter((row) => row.status === "active")
            .map((row) => row.id),
        );
        const mappings = new Set(rows(domains).map((row) => row.client_id));
        const mapped = rows(clients).filter(
          (row) => row.status === "active" && mappings.has(row.id),
        );
        const imports = rows(jobs).filter(
          (row) => row.connection_id && activeConnections.has(row.connection_id),
        );
        const done = imports.filter((row) => row.status === "succeeded").length;
        const failed = imports.filter((row) => row.status === "failed").length;
        return {
          workspace: checked(workspace),
          budget_threshold: rows(budget)[0]?.threshold ?? null,
          summary: {
            active_connections: activeConnections.size,
            mapped_clients: mapped.filter((row) => row.policy !== "excluded").length,
            full_clients: mapped.filter((row) => row.policy === "full").length,
            metadata_clients: mapped.filter((row) => row.policy === "metadata_only").length,
            excluded_clients: mapped.filter((row) => row.policy === "excluded").length,
            imports_total: imports.length,
            imports_done: done,
            imports_failed: failed,
            import_status: failed
              ? "needs_attention"
              : !imports.length
                ? "waiting"
                : done === imports.length
                  ? "ready"
                  : "in_progress",
          },
        };
      }
    }
  }
}

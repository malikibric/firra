import { vaultRecord, type VaultRecord, type VaultRecordsResponse } from "@firra/contracts";
import type { Database } from "@firra/db-types";
import type { SupabaseClient } from "@supabase/supabase-js";
import { instant, parsePage } from "./read-page.js";

export interface VaultQuery {
  limit: number;
  cursor: { created_at: string; id: string } | null;
}
export function parseVaultPage(limit?: string, cursor?: string): VaultQuery | null {
  const parsed = parsePage(limit, cursor, true);
  return parsed
    ? {
        limit: parsed.limit,
        cursor: parsed.cursor
          ? { created_at: parsed.cursor.occurred_at, id: parsed.cursor.id }
          : null,
      }
    : null;
}
export interface VaultRow extends VaultRecord {
  created_at: string;
}
export interface VaultTarget {
  id: string;
  workspace_id: string;
}
export interface VaultMailbox {
  id: string;
  workspace_id: string;
}
export interface VaultStore {
  keepable(userId: string, clientIds: string[]): Promise<string[]>;
  records(userId: string, query: VaultQuery): Promise<VaultRow[]>;
  /** Caller-owned metadata only, used to audit before acknowledging anything. */
  pendingAck(userId: string, ids: string[]): Promise<VaultTarget[]>;
  ack(userId: string, ids: string[]): Promise<number>;
  /** The caller's own active Gmail/Outlook mailboxes (member-owned, or a contributor's own). */
  ownMailboxes(userId: string): Promise<VaultMailbox[]>;
  /** Empty polls still need a tenant-scoped audit, even after every body was purged. */
  auditWorkspaces(userId: string): Promise<string[]>;
}

export function vaultPage(rows: VaultRow[], query: VaultQuery): VaultRecordsResponse {
  const items = rows.slice(0, query.limit).map((row) => vaultRecord.parse(row));
  const last = rows.slice(0, query.limit).at(-1);
  return {
    items,
    next_cursor:
      rows.length > query.limit && last
        ? Buffer.from(`${instant(last.created_at)}|${last.id}`).toString("base64url")
        : null,
  };
}

// Migration 20261002000022 precedes the next generated db-types update. Keep its two columns
// local to this store; generated files remain owned by schema CI.
type SourceTable = Database["public"]["Tables"]["source_record"];
type DeliveryColumns = Record<"vault_user_id" | "vault_delivered_at", string | null>;
type VaultDatabase = Omit<Database, "public"> & {
  public: Omit<Database["public"], "Tables"> & {
    Tables: Omit<Database["public"]["Tables"], "source_record"> & {
      source_record: Omit<SourceTable, "Row" | "Insert" | "Update"> & {
        Row: SourceTable["Row"] & DeliveryColumns;
        Insert: SourceTable["Insert"] & Partial<DeliveryColumns>;
        Update: SourceTable["Update"] & Partial<DeliveryColumns>;
      };
    };
  };
};
const COLUMNS =
  "id, workspace_id, provider, external_id, kind, client_id, thread_key, occurred_at, direction, metadata, deep_link, content_hash, word_count, body_clean, created_at, client!inner(policy)";

/** Service-role access confined to caller-owned vault records, never workspace-role access. */
export class SupabaseVaultStore implements VaultStore {
  private readonly db: SupabaseClient<VaultDatabase>;
  constructor(db: SupabaseClient<Database>) {
    this.db = db;
  }

  async keepable(userId: string, clientIds: string[]): Promise<string[]> {
    const [members, contributors] = await Promise.all([
      this.db.from("membership").select("workspace_id").eq("user_id", userId),
      this.db
        .from("contributor")
        .select("workspace_id")
        .eq("user_id", userId)
        .eq("status", "active"),
    ]);
    if (members.error || contributors.error) throw new Error("vault keep scope lookup failed");
    const workspaces = [
      ...new Set([...members.data, ...contributors.data].map((row) => row.workspace_id)),
    ];
    if (!workspaces.length || !clientIds.length) return [];
    const { data, error } = await this.db
      .from("client")
      .select("id")
      .in("id", [...new Set(clientIds)])
      .in("workspace_id", workspaces)
      .eq("policy", "full");
    if (error) throw new Error("vault keep lookup failed");
    return data.map((row) => row.id);
  }

  /**
   * The mailboxes whose mail may reach this user today: their own mailbox in a workspace where they are
   * still a member, or the mailbox of their active contributor link. A stored recipient alone is not
   * enough (a removed member or revoked contributor keeps a valid session). `statuses` narrows to
   * active mailboxes where only those may act (refetch).
   */
  private async authorizedConnections(
    userId: string,
    statuses: ("active" | "error" | "revoked")[] = ["active", "error", "revoked"],
  ): Promise<VaultMailbox[]> {
    const [members, contributors] = await Promise.all([
      this.db.from("membership").select("workspace_id").eq("user_id", userId),
      this.db.from("contributor").select("id").eq("user_id", userId).eq("status", "active"),
    ]);
    if (members.error || contributors.error) throw new Error("vault scope lookup failed");
    const workspaces = members.data.map((row) => row.workspace_id);
    const links = contributors.data.map((row) => row.id);
    const base = () =>
      this.db
        .from("connection")
        .select("id, workspace_id")
        .eq("mode", "user")
        .in("status", statuses)
        .in("provider", ["gmail", "outlook"]);
    const [owned, linked] = await Promise.all([
      workspaces.length
        ? base().eq("owner_user_id", userId).in("workspace_id", workspaces)
        : Promise.resolve({ data: [] as VaultMailbox[], error: null }),
      links.length
        ? base().in("owner_contributor_id", links)
        : Promise.resolve({ data: [] as VaultMailbox[], error: null }),
    ]);
    if (owned.error || linked.error) throw new Error("vault scope lookup failed");
    const seen = new Map<string, VaultMailbox>();
    for (const row of [...owned.data, ...linked.data]) seen.set(row.id, row);
    return [...seen.values()];
  }

  async records(userId: string, query: VaultQuery): Promise<VaultRow[]> {
    const allowed = (await this.authorizedConnections(userId)).map((row) => row.id);
    if (!allowed.length) return [];
    let request = this.db
      .from("source_record")
      .select(COLUMNS)
      .eq("vault_user_id", userId)
      .in("connection_id", allowed)
      // Only kinds the contract can carry; any other kind would fail the whole page.
      .in("kind", ["email", "calendar_event"])
      .not("body_clean", "is", null)
      .is("vault_delivered_at", null)
      .eq("policy_at_ingest", "full")
      .eq("client.policy", "full")
      .order("created_at", { ascending: false })
      .order("id", { ascending: false })
      .limit(query.limit + 1);
    if (query.cursor) {
      const { created_at, id } = query.cursor;
      request = request.or(
        `created_at.lt.${created_at},and(created_at.eq.${created_at},id.lt.${id})`,
      );
    }
    const { data, error } = await request;
    if (error) throw new Error("vault read failed");
    return data.map((row) => {
      const metadata = row.metadata;
      const subject =
        metadata &&
        typeof metadata === "object" &&
        !Array.isArray(metadata) &&
        typeof metadata.subject === "string"
          ? metadata.subject.trim().slice(0, 300)
          : null;
      return {
        ...vaultRecord.parse({ ...row, subject, body: row.body_clean }),
        created_at: row.created_at,
      };
    });
  }

  async pendingAck(userId: string, ids: string[]): Promise<VaultTarget[]> {
    const allowed = (await this.authorizedConnections(userId)).map((row) => row.id);
    if (!allowed.length) return [];
    const { data, error } = await this.db
      .from("source_record")
      .select("id, workspace_id")
      .eq("vault_user_id", userId)
      .in("connection_id", allowed)
      .is("vault_delivered_at", null)
      .in("id", ids);
    if (error) throw new Error("vault ack lookup failed");
    return data;
  }

  async ack(userId: string, ids: string[]): Promise<number> {
    if (!ids.length) return 0;
    const allowed = (await this.authorizedConnections(userId)).map((row) => row.id);
    if (!allowed.length) return 0;
    const { data, error } = await this.db
      .from("source_record")
      .update({ vault_delivered_at: new Date().toISOString() })
      .eq("vault_user_id", userId)
      .in("connection_id", allowed)
      .is("vault_delivered_at", null)
      .in("id", ids)
      .select("id");
    if (error) throw new Error("vault ack failed");
    return data.length;
  }

  async ownMailboxes(userId: string): Promise<VaultMailbox[]> {
    return this.authorizedConnections(userId, ["active"]);
  }

  async auditWorkspaces(userId: string): Promise<string[]> {
    const [members, contributors, records] = await Promise.all([
      this.db.from("membership").select("workspace_id").eq("user_id", userId),
      this.db
        .from("contributor")
        .select("workspace_id")
        .eq("user_id", userId)
        .eq("status", "active"),
      this.db.from("source_record").select("workspace_id").eq("vault_user_id", userId),
    ]);
    if (members.error || contributors.error || records.error)
      throw new Error("vault audit lookup failed");
    return [
      ...new Set(
        [...members.data, ...contributors.data, ...records.data].map((row) => row.workspace_id),
      ),
    ];
  }
}

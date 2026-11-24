import type { ClaimInviteRequest } from "@firra/contracts";
import type { Database } from "@firra/db-types";
import { createClient } from "@supabase/supabase-js";
import type { ReadCaller } from "./read-store.js";

/** Refusals the routes answer with 404 (an invalid, foreign or revoked invite is the same to the caller). */
export class MeNotFoundError extends Error {}

export interface MeData {
  memberships: { workspace_id: string; workspace_name: string; role: "owner" | "admin" }[];
  contributions: { workspace_id: string; workspace_name: string; contributor_id: string }[];
}
export interface ContributorConnectionRow {
  id: string;
  provider: "gmail" | "google_calendar" | "outlook" | "outlook_calendar" | "email_upload";
  account_label: string;
  status: "active" | "error" | "revoked";
  last_synced_at: string | null;
  last_error_class: string | null;
}

/** What a signed-in user may see about themselves; everything runs as the caller (RLS and RPCs). */
export interface MeStore {
  me(caller: ReadCaller): Promise<MeData>;
  claim(
    caller: ReadCaller,
    request: ClaimInviteRequest,
  ): Promise<{ workspace_id: string; contributor_id: string }>;
  connections(caller: ReadCaller, workspaceId: string): Promise<ContributorConnectionRow[]>;
  disconnect(caller: ReadCaller, connectionId: string): Promise<void>;
}

const FORBIDDEN = "42501";
const NOT_FOUND = "P0002";
const INVALID = "22023";

export class SupabaseMeStore implements MeStore {
  constructor(
    private readonly url: string,
    private readonly publishableKey: string,
  ) {}

  private asCaller(caller: ReadCaller) {
    return createClient<Database>(this.url, this.publishableKey, {
      auth: { persistSession: false, autoRefreshToken: false },
      global: { headers: { Authorization: `Bearer ${caller.token}` } },
    });
  }

  async me(caller: ReadCaller): Promise<MeData> {
    const db = this.asCaller(caller);
    const [memberships, contributions] = await Promise.all([
      db
        .from("membership")
        .select("workspace_id, role, workspace:workspace_id(name)")
        .eq("user_id", caller.userId),
      db.rpc("my_contributions"),
    ]);
    if (memberships.error || contributions.error) throw new Error("me read failed");
    return {
      memberships: memberships.data.map((row) => ({
        workspace_id: row.workspace_id,
        workspace_name: row.workspace.name,
        role: row.role,
      })),
      contributions: contributions.data.map((row) => ({
        workspace_id: row.workspace_id,
        workspace_name: row.workspace_name,
        contributor_id: row.contributor_id,
      })),
    };
  }

  async claim(caller: ReadCaller, request: ClaimInviteRequest) {
    const { data, error } = await this.asCaller(caller).rpc("claim_contributor_invite", {
      p_token: request.token,
    });
    if (error && [FORBIDDEN, NOT_FOUND, INVALID].includes(error.code)) throw new MeNotFoundError();
    const row = data?.[0];
    if (error || !row) throw new Error("claim failed");
    return { workspace_id: row.out_workspace_id, contributor_id: row.out_contributor_id };
  }

  async connections(caller: ReadCaller, workspaceId: string): Promise<ContributorConnectionRow[]> {
    const { data, error } = await this.asCaller(caller)
      .from("connection")
      .select("id, provider, account_label, status, last_synced_at, last_error_class")
      .eq("workspace_id", workspaceId)
      .order("created_at");
    if (error) throw new Error("connections read failed");
    return data;
  }

  async disconnect(caller: ReadCaller, connectionId: string): Promise<void> {
    const { error } = await this.asCaller(caller).rpc("disconnect_own_connection", {
      p_connection_id: connectionId,
    });
    if (error?.code === FORBIDDEN) throw new MeNotFoundError();
    if (error) throw new Error("disconnect failed");
  }
}

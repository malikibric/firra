import type { Database } from "@firra/db-types";
import type { SupabaseClient } from "@supabase/supabase-js";
import type { WorkspaceKeyStore } from "../crypto/keyring.js";

/** Service-role persistence for connect flows. Every query is scoped by workspace or a token hash. */
export type OAuthProvider = "google" | "microsoft";
export type ConnectionProvider = "gmail" | "google_calendar" | "outlook" | "outlook_calendar";

export interface PendingAuthorization {
  workspaceId: string;
  provider: OAuthProvider;
  ownerUserId: string | null;
  ownerContributorId: string | null;
  codeVerifier: string;
  returnTo: string;
}

export interface ContributorLink {
  workspaceId: string;
  contributorId: string;
  email: string;
}

export interface NewConnection {
  workspaceId: string;
  provider: ConnectionProvider;
  /** Defaults to "user" (a person's own OAuth grant). */
  mode?: "user" | "org_delegated" | "org_app";
  ownerUserId: string | null;
  ownerContributorId: string | null;
  accountLabel: string;
  externalAccountId: string;
  scopes: string[];
}

export interface ConnectStore {
  savePending(stateHash: string, pending: PendingAuthorization): Promise<void>;
  /** Deletes and returns the pending authorization if it exists and has not expired. */
  consumePending(stateHash: string): Promise<PendingAuthorization | null>;
  /** The signed-in user's active contributor link in a workspace (account-based flow). */
  contributorByUser(workspaceId: string, userId: string): Promise<ContributorLink | null>;
  /** Creates or refreshes the connection for this provider account; returns its id. */
  upsertConnection(connection: NewConnection): Promise<string>;
  saveToken(workspaceId: string, connectionId: string, ciphertext: string): Promise<void>;
  activateContributor(workspaceId: string, contributorId: string): Promise<void>;
  /** The member's own active Outlook connection (its token holds the verified tenant id). */
  memberOutlookConnection(
    workspaceId: string,
    userId: string,
  ): Promise<{ connectionId: string; ciphertext: string } | null>;
  audit(entry: AuditEntry): Promise<void>;
}

export interface AuditEntry {
  workspaceId: string;
  actorUserId: string;
  action: string;
  targetType?: string;
  targetId?: string;
  metadata?: Record<string, string | number>;
}

const hex = (value: string) => `\\x${value}`;

export class SupabaseConnectStore implements ConnectStore, WorkspaceKeyStore {
  constructor(private readonly db: SupabaseClient<Database>) {}

  async savePending(stateHash: string, pending: PendingAuthorization): Promise<void> {
    const { error } = await this.db.from("oauth_state").insert({
      workspace_id: pending.workspaceId,
      state_hash: hex(stateHash),
      provider: pending.provider,
      owner_user_id: pending.ownerUserId,
      owner_contributor_id: pending.ownerContributorId,
      code_verifier: pending.codeVerifier,
      return_to: pending.returnTo,
    });
    if (error) throw new Error(`oauth state insert failed: ${error.code}`);
  }

  async consumePending(stateHash: string): Promise<PendingAuthorization | null> {
    const { data, error } = await this.db
      .from("oauth_state")
      .delete()
      .eq("state_hash", hex(stateHash))
      .gt("expires_at", new Date().toISOString())
      .select(
        "workspace_id, provider, owner_user_id, owner_contributor_id, code_verifier, return_to",
      )
      .maybeSingle();
    if (error) throw new Error(`oauth state consume failed: ${error.code}`);
    if (data?.provider !== "google" && data?.provider !== "microsoft") return null;
    return {
      workspaceId: data.workspace_id,
      provider: data.provider,
      ownerUserId: data.owner_user_id,
      ownerContributorId: data.owner_contributor_id,
      codeVerifier: data.code_verifier,
      returnTo: data.return_to,
    };
  }

  async contributorByUser(workspaceId: string, userId: string): Promise<ContributorLink | null> {
    const { data, error } = await this.db
      .from("contributor")
      .select("id, workspace_id, email")
      .eq("workspace_id", workspaceId)
      .eq("user_id", userId)
      .eq("status", "active")
      .maybeSingle();
    if (error) throw new Error(`contributor lookup failed: ${error.code}`);
    return data
      ? { workspaceId: data.workspace_id, contributorId: data.id, email: data.email }
      : null;
  }

  async upsertConnection(connection: NewConnection): Promise<string> {
    const { data: existing, error: findError } = await this.db
      .from("connection")
      .select("id")
      .eq("workspace_id", connection.workspaceId)
      .eq("provider", connection.provider)
      .eq("mode", connection.mode ?? "user")
      .eq("external_account_id", connection.externalAccountId)
      .maybeSingle();
    if (findError) throw new Error(`connection lookup failed: ${findError.code}`);
    const fields = {
      account_label: connection.accountLabel,
      owner_user_id: connection.ownerUserId,
      owner_contributor_id: connection.ownerContributorId,
      scopes: connection.scopes,
      status: "active" as const,
      last_error_class: null,
    };
    if (existing) {
      const { error } = await this.db
        .from("connection")
        .update(fields)
        .eq("workspace_id", connection.workspaceId)
        .eq("id", existing.id);
      if (error) throw new Error(`connection update failed: ${error.code}`);
      return existing.id;
    }
    const { data, error } = await this.db
      .from("connection")
      .insert({
        ...fields,
        workspace_id: connection.workspaceId,
        provider: connection.provider,
        mode: connection.mode ?? "user",
        external_account_id: connection.externalAccountId,
      })
      .select("id")
      .single();
    if (error) throw new Error(`connection insert failed: ${error.code}`);
    return data.id;
  }

  async saveToken(workspaceId: string, connectionId: string, ciphertext: string): Promise<void> {
    const { error } = await this.db
      .from("connection")
      .update({ token_ciphertext: ciphertext })
      .eq("workspace_id", workspaceId)
      .eq("id", connectionId);
    if (error) throw new Error(`token save failed: ${error.code}`);
  }

  async activateContributor(workspaceId: string, contributorId: string): Promise<void> {
    const { error } = await this.db
      .from("contributor")
      .update({ status: "active" })
      .eq("workspace_id", workspaceId)
      .eq("id", contributorId)
      .neq("status", "revoked");
    if (error) throw new Error(`contributor update failed: ${error.code}`);
  }

  async memberOutlookConnection(workspaceId: string, userId: string) {
    const { data, error } = await this.db
      .from("connection")
      .select("id, token_ciphertext")
      .eq("workspace_id", workspaceId)
      .eq("owner_user_id", userId)
      .eq("provider", "outlook")
      .eq("mode", "user")
      .eq("status", "active")
      .not("token_ciphertext", "is", null)
      .order("created_at")
      .limit(1)
      .maybeSingle();
    if (error) throw new Error(`outlook connection lookup failed: ${error.code}`);
    return data?.token_ciphertext
      ? { connectionId: data.id, ciphertext: data.token_ciphertext }
      : null;
  }

  async audit(entry: AuditEntry): Promise<void> {
    const { error } = await this.db.from("audit_log").insert({
      workspace_id: entry.workspaceId,
      actor_type: "user",
      actor_id: entry.actorUserId,
      action: entry.action,
      target_type: entry.targetType ?? null,
      target_id: entry.targetId ?? null,
      metadata: entry.metadata ?? {},
    });
    if (error) throw new Error(`audit insert failed: ${error.code}`);
  }

  // WorkspaceKeyStore (public.workspace_key); bytea travels as \x-prefixed hex through PostgREST.
  async get(workspaceId: string): Promise<Buffer | null> {
    const { data, error } = await this.db
      .from("workspace_key")
      .select("wrapped_dek")
      .eq("workspace_id", workspaceId)
      .maybeSingle();
    if (error) throw new Error(`workspace key lookup failed: ${error.code}`);
    return data ? Buffer.from(data.wrapped_dek.replace(/^\\x/, ""), "hex") : null;
  }

  async insertIfAbsent(workspaceId: string, wrappedDek: Buffer, kekName: string): Promise<Buffer> {
    const { error } = await this.db.from("workspace_key").upsert(
      {
        workspace_id: workspaceId,
        wrapped_dek: hex(wrappedDek.toString("hex")),
        kek_name: kekName,
      },
      { onConflict: "workspace_id", ignoreDuplicates: true },
    );
    if (error) throw new Error(`workspace key insert failed: ${error.code}`);
    const stored = await this.get(workspaceId);
    if (!stored) throw new Error("workspace key missing after insert");
    return stored;
  }
}

import type { CreateMcpTokenRequest, McpTokenCreated, McpTokenItem } from "@firra/contracts";
import type { Database } from "@firra/db-types";
import { createClient } from "@supabase/supabase-js";
import type { ReadCaller } from "../lib/read-store.js";

/** Why a token operation was refused; the route maps both to 404 so existence never leaks. */
export class TokenForbiddenError extends Error {}

/** Token management for owners and admins, run as the caller so the RPCs authorize and audit. */
export interface McpTokenAdmin {
  create(
    caller: ReadCaller,
    workspaceId: string,
    request: CreateMcpTokenRequest,
  ): Promise<McpTokenCreated>;
  list(caller: ReadCaller, workspaceId: string): Promise<McpTokenItem[]>;
  revoke(caller: ReadCaller, workspaceId: string, tokenId: string): Promise<void>;
}

const FORBIDDEN = "42501";
const TOO_MANY = "54000";

export class TooManyTokensError extends Error {}

export class SupabaseMcpTokenAdmin implements McpTokenAdmin {
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

  async create(
    caller: ReadCaller,
    workspaceId: string,
    request: CreateMcpTokenRequest,
  ): Promise<McpTokenCreated> {
    const { data, error } = await this.asCaller(caller).rpc("create_mcp_token", {
      p_workspace_id: workspaceId,
      p_name: request.name,
      p_days: request.days,
    });
    if (error?.code === FORBIDDEN) throw new TokenForbiddenError();
    if (error?.code === TOO_MANY) throw new TooManyTokensError();
    const row = data?.[0];
    if (error || !row) throw new Error("token create failed");
    return {
      id: row.token_id,
      token: row.token,
      token_prefix: row.token_prefix,
      expires_at: row.expires_at,
    };
  }

  async list(caller: ReadCaller, workspaceId: string): Promise<McpTokenItem[]> {
    const { data, error } = await this.asCaller(caller)
      .from("mcp_token")
      .select(
        "id, user_id, name, token_prefix, scopes, expires_at, last_used_at, revoked_at, created_at",
      )
      .eq("workspace_id", workspaceId)
      .order("created_at", { ascending: false })
      .limit(200);
    if (error) throw new Error("token list failed");
    return data;
  }

  async revoke(caller: ReadCaller, workspaceId: string, tokenId: string): Promise<void> {
    const db = this.asCaller(caller);
    // The path names the workspace: a token of another workspace is simply not found.
    const { data, error: lookupError } = await db
      .from("mcp_token")
      .select("id")
      .eq("workspace_id", workspaceId)
      .eq("id", tokenId)
      .maybeSingle();
    if (lookupError) throw new Error("token revoke failed");
    if (!data) throw new TokenForbiddenError();
    const { error } = await db.rpc("revoke_mcp_token", {
      p_token_id: tokenId,
    });
    if (error?.code === FORBIDDEN) throw new TokenForbiddenError();
    if (error) throw new Error("token revoke failed");
  }
}

import type {
  FeedbackRequest,
  OutcomeRequest,
  RoutineKind,
  RoutineEnabledRequest,
} from "@firra/contracts";
import type { Database } from "@firra/db-types";
import { createClient } from "@supabase/supabase-js";
import type { ReadCaller } from "./read-store.js";

export class WriteForbiddenError extends Error {}
export interface WriteStore {
  setRoutineEnabled(
    caller: ReadCaller,
    workspaceId: string,
    kind: RoutineKind,
    request: RoutineEnabledRequest,
  ): Promise<string>;
  feedback(
    caller: ReadCaller,
    workspaceId: string,
    cardId: string,
    request: FeedbackRequest,
  ): Promise<string>;
  outcome(
    caller: ReadCaller,
    workspaceId: string,
    clientId: string,
    request: OutcomeRequest,
  ): Promise<string>;
  deleteOutcome(caller: ReadCaller, workspaceId: string, outcomeId: string): Promise<void>;
}
/** Caller-token RPCs own mutation authorization and audit; never use the service role. */
export class SupabaseWriteStore implements WriteStore {
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
  async setRoutineEnabled(
    caller: ReadCaller,
    workspaceId: string,
    kind: RoutineKind,
    request: RoutineEnabledRequest,
  ) {
    const { data, error } = await this.asCaller(caller).rpc("set_routine_enabled", {
      p_workspace_id: workspaceId,
      p_kind: kind,
      p_enabled: request.enabled,
    });
    if (error?.code === "42501") throw new WriteForbiddenError();
    if (error || !data) throw new Error("routine switch failed");
    return data;
  }
  private async visible(
    caller: ReadCaller,
    workspaceId: string,
    table: "client" | "decision_card" | "outcome",
    id: string,
  ) {
    const db = this.asCaller(caller);
    const base = db.from(table).select("id").eq("workspace_id", workspaceId).eq("id", id);
    // An excluded client does not exist for the API (the setup pages still list it).
    const { data, error } =
      table === "client"
        ? await db
            .from("client")
            .select("id")
            .eq("workspace_id", workspaceId)
            .eq("id", id)
            .neq("policy", "excluded")
            .maybeSingle()
        : await base.maybeSingle();
    if (error) throw new Error("mutation target read failed");
    if (!data) throw new WriteForbiddenError();
  }
  async feedback(
    caller: ReadCaller,
    workspaceId: string,
    cardId: string,
    request: FeedbackRequest,
  ) {
    await this.visible(caller, workspaceId, "decision_card", cardId);
    const { data, error } = await this.asCaller(caller).rpc("record_decision_feedback", {
      p_decision_card_id: cardId,
      p_verdict: request.verdict,
      ...(request.action != null ? { p_action: request.action } : {}),
      ...(request.note != null ? { p_note: request.note } : {}),
    });
    if (error?.code === "42501") throw new WriteForbiddenError();
    if (error || !data) throw new Error("feedback write failed");
    return data;
  }
  async outcome(
    caller: ReadCaller,
    workspaceId: string,
    clientId: string,
    request: OutcomeRequest,
  ) {
    await this.visible(caller, workspaceId, "client", clientId);
    const { data, error } = await this.asCaller(caller).rpc("record_client_outcome", {
      p_client_id: clientId,
      p_event: request.event,
      p_occurred_at: request.occurred_at,
      ...(request.notice_at != null ? { p_notice_at: request.notice_at } : {}),
      ...(request.first_known_at != null ? { p_first_known_at: request.first_known_at } : {}),
      ...(request.amount_change != null ? { p_amount_change: request.amount_change } : {}),
      ...(request.currency != null ? { p_currency: request.currency } : {}),
      ...(request.note != null ? { p_note: request.note } : {}),
    });
    if (error?.code === "42501") throw new WriteForbiddenError();
    if (error || !data) throw new Error("outcome write failed");
    return data;
  }
  async deleteOutcome(caller: ReadCaller, workspaceId: string, outcomeId: string) {
    await this.visible(caller, workspaceId, "outcome", outcomeId);
    const { error } = await this.asCaller(caller).rpc("delete_client_outcome", {
      p_outcome_id: outcomeId,
    });
    if (error?.code === "42501") throw new WriteForbiddenError();
    if (error) throw new Error("outcome delete failed");
  }
}

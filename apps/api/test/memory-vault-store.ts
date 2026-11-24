import type {
  VaultMailbox,
  VaultQuery,
  VaultRow,
  VaultStore,
  VaultTarget,
} from "../src/lib/vault-store.js";
import { instant } from "../src/lib/read-page.js";

export interface OwnedVaultRow extends VaultRow {
  vault_user_id: string | null;
  vault_delivered_at: string | null;
  policy: "full" | "metadata_only" | "excluded";
}
export class MemoryVaultStore implements VaultStore {
  rows: OwnedVaultRow[] = [];
  clients: { id: string; workspace_id: string; policy: OwnedVaultRow["policy"] }[] = [];
  contributors: { user_id: string; workspace_id: string; status: "active" | "revoked" }[] = [];
  keepable(userId: string, clientIds: string[]): Promise<string[]> {
    const scopes = new Set([
      ...(this.scopes.get(userId) ?? []),
      ...this.contributors
        .filter((row) => row.user_id === userId && row.status === "active")
        .map((row) => row.workspace_id),
    ]);
    return Promise.resolve([
      ...new Set(
        this.clients
          .filter(
            (row) =>
              clientIds.includes(row.id) && scopes.has(row.workspace_id) && row.policy === "full",
          )
          .map((row) => row.id),
      ),
    ]);
  }
  scopes = new Map<string, string[]>();
  mailboxes = new Map<string, VaultMailbox[]>();
  readCalls = 0;
  ackCalls = 0;
  records(userId: string, query: VaultQuery): Promise<VaultRow[]> {
    this.readCalls++;
    return Promise.resolve(
      this.rows
        .filter(
          (row) =>
            row.vault_user_id === userId &&
            row.vault_delivered_at === null &&
            row.policy === "full" &&
            (!query.cursor ||
              instant(row.created_at) < query.cursor.created_at ||
              (instant(row.created_at) === query.cursor.created_at && row.id < query.cursor.id)),
        )
        .sort(
          (a, b) =>
            instant(b.created_at).localeCompare(instant(a.created_at)) || b.id.localeCompare(a.id),
        )
        .slice(0, query.limit + 1),
    );
  }
  pendingAck(userId: string, ids: string[]): Promise<VaultTarget[]> {
    return Promise.resolve(
      this.rows
        .filter(
          (row) =>
            row.vault_user_id === userId && row.vault_delivered_at === null && ids.includes(row.id),
        )
        .map(({ id, workspace_id }) => ({ id, workspace_id })),
    );
  }
  ack(userId: string, ids: string[]): Promise<number> {
    this.ackCalls++;
    let count = 0;
    for (const row of this.rows) {
      if (row.vault_user_id === userId && row.vault_delivered_at === null && ids.includes(row.id)) {
        row.vault_delivered_at = new Date().toISOString();
        count++;
      }
    }
    return Promise.resolve(count);
  }
  ownMailboxes(userId: string): Promise<VaultMailbox[]> {
    return Promise.resolve(this.mailboxes.get(userId) ?? []);
  }
  auditWorkspaces(userId: string): Promise<string[]> {
    return Promise.resolve([
      ...new Set([
        ...(this.scopes.get(userId) ?? []),
        ...this.contributors
          .filter((row) => row.user_id === userId && row.status === "active")
          .map((row) => row.workspace_id),
        ...this.rows.filter((row) => row.vault_user_id === userId).map((row) => row.workspace_id),
      ]),
    ]);
  }
}

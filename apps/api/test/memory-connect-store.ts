import { randomUUID } from "node:crypto";
import type { WorkspaceKeyStore } from "../src/crypto/keyring.js";
import type {
  AuditEntry,
  ConnectStore,
  ContributorLink,
  NewConnection,
  PendingAuthorization,
} from "../src/connect/store.js";

export class MemoryConnectStore implements ConnectStore, WorkspaceKeyStore {
  pending = new Map<string, PendingAuthorization & { expired?: boolean }>();
  connections = new Map<string, NewConnection & { id: string; token?: string }>();
  audits: AuditEntry[] = [];
  activated: string[] = [];
  keys = new Map<string, Buffer>();

  savePending(stateHash: string, pending: PendingAuthorization): Promise<void> {
    this.pending.set(stateHash, pending);
    return Promise.resolve();
  }

  consumePending(stateHash: string): Promise<PendingAuthorization | null> {
    const found = this.pending.get(stateHash);
    this.pending.delete(stateHash);
    return Promise.resolve(found && !found.expired ? found : null);
  }

  /** Contributors who signed in, keyed by `${workspaceId}:${userId}`. */
  contributorUsers = new Map<string, ContributorLink>();

  contributorByUser(workspaceId: string, userId: string): Promise<ContributorLink | null> {
    return Promise.resolve(this.contributorUsers.get(`${workspaceId}:${userId}`) ?? null);
  }

  upsertConnection(connection: NewConnection): Promise<string> {
    const existing = [...this.connections.values()].find(
      (c) =>
        c.workspaceId === connection.workspaceId &&
        c.externalAccountId === connection.externalAccountId &&
        c.provider === connection.provider &&
        (c.mode ?? "user") === (connection.mode ?? "user"),
    );
    const id = existing?.id ?? randomUUID();
    this.connections.set(id, { ...connection, id });
    return Promise.resolve(id);
  }

  saveToken(_workspaceId: string, connectionId: string, ciphertext: string): Promise<void> {
    const connection = this.connections.get(connectionId);
    if (connection) connection.token = ciphertext;
    return Promise.resolve();
  }

  activateContributor(_workspaceId: string, contributorId: string): Promise<void> {
    this.activated.push(contributorId);
    return Promise.resolve();
  }

  memberOutlookConnection(workspaceId: string, userId: string) {
    const found = [...this.connections.values()].find(
      (c) =>
        c.workspaceId === workspaceId &&
        c.ownerUserId === userId &&
        c.provider === "outlook" &&
        (c.mode ?? "user") === "user" &&
        c.token,
    );
    return Promise.resolve(
      found?.token ? { connectionId: found.id, ciphertext: found.token } : null,
    );
  }

  audit(entry: AuditEntry): Promise<void> {
    this.audits.push(entry);
    return Promise.resolve();
  }

  get(workspaceId: string): Promise<Buffer | null> {
    return Promise.resolve(this.keys.get(workspaceId) ?? null);
  }

  insertIfAbsent(workspaceId: string, wrapped: Buffer): Promise<Buffer> {
    if (!this.keys.has(workspaceId)) this.keys.set(workspaceId, wrapped);
    const stored = this.keys.get(workspaceId);
    if (!stored) throw new Error("missing");
    return Promise.resolve(stored);
  }
}

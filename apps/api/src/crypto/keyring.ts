import { decrypt, encrypt, newDek, tokenAad } from "./envelope.js";
import type { Kek } from "./kek.js";

/** Persistence of wrapped DEKs (public.workspace_key, service role only). */
export interface WorkspaceKeyStore {
  get(workspaceId: string): Promise<Buffer | null>;
  /** Inserts unless a key exists; returns the stored key either way. */
  insertIfAbsent(workspaceId: string, wrappedDek: Buffer, kekName: string): Promise<Buffer>;
}

/** Per-workspace DEKs, created on first use and cached unwrapped for the process lifetime. */
export class Keyring {
  private readonly cache = new Map<string, Buffer>();

  constructor(
    private readonly kek: Kek,
    private readonly store: WorkspaceKeyStore,
  ) {}

  async dek(workspaceId: string): Promise<Buffer> {
    const cached = this.cache.get(workspaceId);
    if (cached) return cached;
    const wrapped =
      (await this.store.get(workspaceId)) ??
      (await this.store.insertIfAbsent(workspaceId, await this.kek.wrap(newDek()), this.kek.name));
    const dek = await this.kek.unwrap(wrapped);
    this.cache.set(workspaceId, dek);
    return dek;
  }

  async encryptToken(workspaceId: string, connectionId: string, token: string): Promise<string> {
    return encrypt(await this.dek(workspaceId), token, tokenAad(workspaceId, connectionId));
  }

  async decryptToken(workspaceId: string, connectionId: string, blob: string): Promise<string> {
    return decrypt(await this.dek(workspaceId), blob, tokenAad(workspaceId, connectionId));
  }
}

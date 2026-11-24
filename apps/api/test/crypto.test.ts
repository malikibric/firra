import { readFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { describe, expect, it } from "vitest";
import { DecryptionError, decrypt, encrypt, newDek, tokenAad } from "../src/crypto/envelope.js";
import { LocalKek } from "../src/crypto/kek.js";
import { Keyring, type WorkspaceKeyStore } from "../src/crypto/keyring.js";

interface Vectors {
  dek: string;
  vectors: { plaintext: string; aad: string; iv: string; ciphertext: string }[];
  local_kek: { kek_hex: string; wrapped_dek: string };
}

// Shared with services/worker/tests/unit/test_crypto.py: both languages must agree.
const fixtures = JSON.parse(
  readFileSync(
    new URL("../../../packages/contracts/fixtures/crypto_vectors.json", import.meta.url),
    "utf8",
  ),
) as Vectors;
const DEK = Buffer.from(fixtures.dek, "base64url");

describe("envelope v1", () => {
  it.each(fixtures.vectors)("matches the shared vector for aad $aad", (vector) => {
    expect(decrypt(DEK, vector.ciphertext, vector.aad)).toBe(vector.plaintext);
    expect(encrypt(DEK, vector.plaintext, vector.aad, Buffer.from(vector.iv, "base64url"))).toBe(
      vector.ciphertext,
    );
  });

  it("rejects tampering and wrong row binding", () => {
    const dek = newDek();
    const blob = encrypt(dek, "secret", tokenAad("ws", "conn-a"));
    expect(() => decrypt(dek, blob, tokenAad("ws", "conn-b"))).toThrow(DecryptionError);
    expect(() => decrypt(dek, blob.replace("v1.", "v2."), tokenAad("ws", "conn-a"))).toThrow(
      DecryptionError,
    );
    expect(() => decrypt(dek, `${blob}x`, tokenAad("ws", "conn-a"))).toThrow(DecryptionError);
    expect(() => decrypt(dek, "garbage", tokenAad("ws", "conn-a"))).toThrow(DecryptionError);
  });
});

describe("LocalKek", () => {
  it("unwraps a key wrapped by the Python worker", async () => {
    const kek = new LocalKek(Buffer.from(fixtures.local_kek.kek_hex, "hex"));
    const dek = await kek.unwrap(Buffer.from(fixtures.local_kek.wrapped_dek, "utf8"));
    expect(dek.equals(Buffer.from(Array.from({ length: 32 }, (_, i) => i)))).toBe(true);
  });
});

describe("Keyring", () => {
  class MemoryStore implements WorkspaceKeyStore {
    rows = new Map<string, Buffer>();
    get(workspaceId: string) {
      return Promise.resolve(this.rows.get(workspaceId) ?? null);
    }
    insertIfAbsent(workspaceId: string, wrapped: Buffer) {
      if (!this.rows.has(workspaceId)) this.rows.set(workspaceId, wrapped);
      const stored = this.rows.get(workspaceId);
      if (!stored) throw new Error("missing");
      return Promise.resolve(stored);
    }
  }

  it("encrypts tokens with a per-workspace key that survives a restart", async () => {
    const store = new MemoryStore();
    const kek = new LocalKek(Buffer.alloc(32, 7));
    const [wsA, wsB, conn] = [randomUUID(), randomUUID(), randomUUID()];
    const blob = await new Keyring(kek, store).encryptToken(wsA, conn, '{"refresh_token":"x"}');
    const restarted = new Keyring(kek, store);
    expect(await restarted.decryptToken(wsA, conn, blob)).toBe('{"refresh_token":"x"}');
    await expect(restarted.decryptToken(wsB, conn, blob)).rejects.toThrow(DecryptionError);
    expect(store.rows.size).toBe(2);
  });
});

describe("CloudKmsKek", () => {
  it("wraps through the KMS REST API and validates responses", async () => {
    const { CloudKmsKek } = await import("../src/crypto/kek.js");
    const calls: string[] = [];
    const client = Promise.resolve({
      request: (options: { url: string; data: unknown }) => {
        calls.push(options.url);
        const data = options.data as Record<string, string>;
        if (options.url.endsWith(":encrypt"))
          return Promise.resolve({ data: { ciphertext: data.plaintext } });
        if (options.url.endsWith(":decrypt"))
          return Promise.resolve({ data: { plaintext: data.ciphertext } });
        return Promise.resolve({ data: { unexpected: true } });
      },
    });
    const name = "projects/p/locations/europe-west3/keyRings/firra/cryptoKeys/token-kek";
    const kek = new CloudKmsKek(name, client);
    const dek = newDek();
    expect((await kek.unwrap(await kek.wrap(dek))).equals(dek)).toBe(true);
    expect(calls).toEqual([
      `https://cloudkms.googleapis.com/v1/${name}:encrypt`,
      `https://cloudkms.googleapis.com/v1/${name}:decrypt`,
    ]);
  });
});

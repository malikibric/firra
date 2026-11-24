/**
 * AES-256-GCM envelope format shared with services/worker (firra/crypto/envelope.py):
 * `v1.<base64url(iv)>.<base64url(ciphertext || tag)>`, 12-byte IV, 16-byte tag, row-bound AAD.
 */
import { createCipheriv, createDecipheriv, randomBytes } from "node:crypto";

const VERSION = "v1";
const IV_BYTES = 12;
const TAG_BYTES = 16;

export class DecryptionError extends Error {
  constructor() {
    super("cannot decrypt");
    this.name = "DecryptionError";
  }
}

export function newDek(): Buffer {
  return randomBytes(32);
}

export function tokenAad(workspaceId: string, connectionId: string): string {
  return `${workspaceId}:${connectionId}`;
}

export function encrypt(
  dek: Buffer,
  plaintext: string,
  aad: string,
  iv: Buffer = randomBytes(IV_BYTES),
): string {
  if (dek.length !== 32 || iv.length !== IV_BYTES) throw new Error("invalid key or iv length");
  const cipher = createCipheriv("aes-256-gcm", dek, iv, { authTagLength: TAG_BYTES });
  cipher.setAAD(Buffer.from(aad, "utf8"));
  const sealed = Buffer.concat([
    cipher.update(plaintext, "utf8"),
    cipher.final(),
    cipher.getAuthTag(),
  ]);
  return `${VERSION}.${iv.toString("base64url")}.${sealed.toString("base64url")}`;
}

export function decrypt(dek: Buffer, ciphertext: string, aad: string): string {
  const [version, ivText, sealedText, extra] = ciphertext.split(".");
  if (version !== VERSION || !ivText || sealedText === undefined || extra !== undefined)
    throw new DecryptionError();
  const iv = Buffer.from(ivText, "base64url");
  const sealed = Buffer.from(sealedText, "base64url");
  if (iv.length !== IV_BYTES || sealed.length < TAG_BYTES) throw new DecryptionError();
  try {
    // Fixed tag length: truncated tags must never be accepted.
    const decipher = createDecipheriv("aes-256-gcm", dek, iv, { authTagLength: TAG_BYTES });
    decipher.setAAD(Buffer.from(aad, "utf8"));
    decipher.setAuthTag(sealed.subarray(sealed.length - TAG_BYTES));
    return Buffer.concat([
      decipher.update(sealed.subarray(0, sealed.length - TAG_BYTES)),
      decipher.final(),
    ]).toString("utf8");
  } catch {
    throw new DecryptionError();
  }
}

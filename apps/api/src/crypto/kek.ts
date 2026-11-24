import { GoogleAuth } from "google-auth-library";
import { z } from "zod";
import { decrypt, encrypt } from "./envelope.js";

/** Key-encryption key that wraps per-workspace DEKs. Mirrors services/worker/firra/crypto/kek.py. */
export interface Kek {
  readonly name: string;
  wrap(dek: Buffer): Promise<Buffer>;
  unwrap(wrapped: Buffer): Promise<Buffer>;
}

/** Static key for local/test only (the env schema refuses it elsewhere). */
export class LocalKek implements Kek {
  readonly name = "local";

  constructor(private readonly key: Buffer) {}

  wrap(dek: Buffer): Promise<Buffer> {
    return Promise.resolve(Buffer.from(encrypt(this.key, dek.toString("base64"), "kek"), "utf8"));
  }

  unwrap(wrapped: Buffer): Promise<Buffer> {
    return Promise.resolve(
      Buffer.from(decrypt(this.key, wrapped.toString("utf8"), "kek"), "base64"),
    );
  }
}

export interface KmsClient {
  request(options: { url: string; method: "POST"; data: unknown }): Promise<{ data: unknown }>;
}

const encryptResponse = z.object({ ciphertext: z.string() });
const decryptResponse = z.object({ plaintext: z.string() });

/** Cloud KMS REST API with the service's own identity (no SDK, no keys). */
export class CloudKmsKek implements Kek {
  constructor(
    readonly name: string,
    private readonly client: Promise<KmsClient> = new GoogleAuth({
      scopes: ["https://www.googleapis.com/auth/cloudkms"],
    }).getClient(),
  ) {}

  private async call(
    method: "encrypt" | "decrypt",
    data: Record<string, string>,
  ): Promise<unknown> {
    const client = await this.client;
    const response = await client.request({
      url: `https://cloudkms.googleapis.com/v1/${this.name}:${method}`,
      method: "POST",
      data,
    });
    return response.data;
  }

  async wrap(dek: Buffer): Promise<Buffer> {
    const body = encryptResponse.parse(
      await this.call("encrypt", { plaintext: dek.toString("base64") }),
    );
    return Buffer.from(body.ciphertext, "base64");
  }

  async unwrap(wrapped: Buffer): Promise<Buffer> {
    const body = decryptResponse.parse(
      await this.call("decrypt", { ciphertext: wrapped.toString("base64") }),
    );
    return Buffer.from(body.plaintext, "base64");
  }
}

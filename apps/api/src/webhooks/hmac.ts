import { createHmac, timingSafeEqual } from "node:crypto";
import type { WebhookRequest, WebhookVerifier } from "./verifier.js";

export interface HmacVerifierOptions {
  secret: string;
  signatureHeader: string;
  /** Builds the signed string, e.g. Slack: `v0:${timestamp}:${body}`. */
  signedContent: (body: string, timestamp: string | null) => string;
  encoding: "hex" | "base64";
  /** Prefix in the header value, e.g. "v0=" or "sha256=". */
  signaturePrefix?: string;
  /** When set, requests older/newer than `toleranceSeconds` are rejected (replay protection). */
  timestampHeader?: string;
  toleranceSeconds?: number;
  now?: () => number;
}

/** Generic HMAC-SHA256 webhook verifier with constant-time comparison. */
export function hmacSha256Verifier(options: HmacVerifierOptions): WebhookVerifier {
  const tolerance = options.toleranceSeconds ?? 300;
  const now = options.now ?? (() => Date.now() / 1000);
  const prefix = options.signaturePrefix ?? "";

  return {
    verify({ rawBody, headers }: WebhookRequest): boolean {
      const header = headers.get(options.signatureHeader);
      if (!header?.startsWith(prefix)) return false;

      let timestamp: string | null = null;
      if (options.timestampHeader) {
        timestamp = headers.get(options.timestampHeader);
        const seconds = Number(timestamp);
        if (!timestamp || !Number.isFinite(seconds) || Math.abs(now() - seconds) > tolerance)
          return false;
      }

      const expected = createHmac("sha256", options.secret)
        .update(options.signedContent(rawBody, timestamp))
        .digest();
      let received: Buffer;
      try {
        received = Buffer.from(header.slice(prefix.length), options.encoding);
      } catch {
        return false;
      }
      return received.length === expected.length && timingSafeEqual(received, expected);
    },
  };
}

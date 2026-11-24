import { createHash, randomBytes } from "node:crypto";

export function randomToken(bytes = 32): string {
  return randomBytes(bytes).toString("base64url");
}

/** RFC 7636 S256 challenge. */
export function codeChallenge(verifier: string): string {
  return createHash("sha256").update(verifier).digest("base64url");
}

export function sha256Hex(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}

/** Same-app web paths or the exact native scheme with a restricted in-app path. */
export function safeReturnPath(value: unknown): string | null {
  if (typeof value !== "string" || value.length > 500 || /[\s\p{Cc}\\]/u.test(value)) return null;
  if (/^firra:\/\/(?!.*\/\/)[a-z0-9/_-]+$/.test(value)) return value;
  if (!/^\/[^/\\]/.test(value)) return null;
  return value;
}

/** Revalidate stored destinations as well as start input; legacy/corrupt state cannot redirect off-app. */
export function returnUrl(value: unknown, webUrl: string | undefined): URL | null {
  const path = safeReturnPath(value);
  if (path?.startsWith("firra://")) return new URL(path);
  if (!webUrl) return null;
  const base = new URL(webUrl);
  const target = new URL(path ?? "/", base);
  return target.origin === base.origin ? target : new URL("/", base);
}

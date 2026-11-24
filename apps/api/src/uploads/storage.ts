/**
 * Where uploaded mail exports (.mbox/.eml) go before the worker ingests them.
 *
 * Cloud: a GCS resumable upload session in the raw bucket (7-day lifecycle). The Mac sends the
 * file straight to GCS, so Cloud Run's request size limit does not apply. The session URI is a
 * bearer credential for exactly one object; it is handed only to the signed-in owner/admin who
 * asked for it. Docs checked: JSON API `uploadType=resumable` (X-Upload-Content-Type/-Length,
 * `Origin` for browser uploads with bucket CORS, session valid for a week), object metadata GET.
 *
 * Local/test: the api stores the file itself under LOCAL_RAW_DIR, where the local worker reads it.
 */
import { randomBytes } from "node:crypto";
import { mkdir, stat, writeFile } from "node:fs/promises";
import { dirname, resolve, sep } from "node:path";
import { GoogleAuth } from "google-auth-library";

export interface UploadStorage {
  /** gs:// bucket name the worker's raw store reads from ("local" in local/test). */
  readonly bucket: string;
  /** Returns a session URL the Mac PUTs the file to. */
  createUpload(objectName: string, size: number): Promise<string>;
  /** Stored size in bytes, or null when the object does not exist (yet). */
  objectSize(objectName: string): Promise<number | null>;
}

// Fixed session-init origin for native Mac uploads; no browser site or WEB_URL is needed.
// URLSession does not enforce browser CORS. This value is a header only, never a request target.
// Docs checked 2026-10-02: https://docs.cloud.google.com/storage/docs/resumable-uploads
export const UPLOAD_ORIGIN = "https://firra.app";

const OBJECT_NAME = /^uploads\/[0-9a-f-]{36}\/[0-9a-f-]{36}\/[0-9a-f-]{36}\.(mbox|eml)$/;

function checkedName(objectName: string): string {
  if (!OBJECT_NAME.test(objectName)) throw new Error("invalid upload object name");
  return objectName;
}

export class GcsUploadStorage implements UploadStorage {
  private readonly auth = new GoogleAuth({
    scopes: ["https://www.googleapis.com/auth/devstorage.read_write"],
  });

  constructor(
    readonly bucket: string,
    private readonly fetchImpl: typeof fetch = fetch,
  ) {}

  private async token(): Promise<string> {
    const token = await this.auth.getAccessToken();
    if (!token) throw new Error("no access token for GCS");
    return token;
  }

  async createUpload(objectName: string, size: number): Promise<string> {
    const url = new URL(`https://storage.googleapis.com/upload/storage/v1/b/${this.bucket}/o`);
    url.searchParams.set("uploadType", "resumable");
    url.searchParams.set("name", checkedName(objectName));
    // Never overwrite: each upload gets a fresh object name.
    url.searchParams.set("ifGenerationMatch", "0");
    const response = await this.fetchImpl(url, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${await this.token()}`,
        "Content-Type": "application/json",
        "X-Upload-Content-Type": "application/octet-stream",
        "X-Upload-Content-Length": String(size),
        Origin: UPLOAD_ORIGIN,
      },
      body: "{}",
      signal: AbortSignal.timeout(15_000),
    });
    const location = response.headers.get("location");
    if (!response.ok || !location?.startsWith("https://storage.googleapis.com/"))
      throw new Error(`resumable upload init failed: ${String(response.status)}`);
    return location;
  }

  async objectSize(objectName: string): Promise<number | null> {
    const url = `https://storage.googleapis.com/storage/v1/b/${this.bucket}/o/${encodeURIComponent(checkedName(objectName))}?fields=size`;
    const response = await this.fetchImpl(url, {
      headers: { Authorization: `Bearer ${await this.token()}` },
      signal: AbortSignal.timeout(15_000),
    });
    if (response.status === 404) return null;
    if (!response.ok) throw new Error(`object lookup failed: ${String(response.status)}`);
    const body = (await response.json()) as { size?: string };
    return Number(body.size ?? "0");
  }
}

/** Local/test only: one-time upload tokens kept in memory, files written under `root`. */
export class LocalUploadStorage implements UploadStorage {
  readonly bucket = "local";
  private readonly pending = new Map<string, { objectName: string; size: number }>();

  constructor(
    private readonly root: string,
    private readonly apiUrl: string,
  ) {}

  private path(objectName: string): string {
    const base = resolve(this.root);
    const path = resolve(base, checkedName(objectName));
    if (!path.startsWith(base + sep)) throw new Error("invalid upload object name");
    return path;
  }

  // eslint-disable-next-line @typescript-eslint/require-await -- same async contract as GCS
  async createUpload(objectName: string, size: number): Promise<string> {
    const token = randomBytes(24).toString("base64url");
    this.pending.set(token, { objectName: checkedName(objectName), size });
    return `${this.apiUrl}/local-uploads/${token}`;
  }

  /** Stores the body for a token from createUpload; false if the token or size is wrong. */
  async receive(token: string, body: Uint8Array): Promise<boolean> {
    const pending = this.pending.get(token);
    if (pending?.size !== body.byteLength) return false;
    this.pending.delete(token);
    const path = this.path(pending.objectName);
    await mkdir(dirname(path), { recursive: true });
    await writeFile(path, body, { flag: "wx" });
    return true;
  }

  async objectSize(objectName: string): Promise<number | null> {
    try {
      return (await stat(this.path(objectName))).size;
    } catch {
      return null;
    }
  }
}

import { randomUUID } from "node:crypto";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { MAX_UPLOAD_BYTES } from "../src/routes/uploads.js";
import {
  UPLOAD_ORIGIN,
  GcsUploadStorage,
  LocalUploadStorage,
  type UploadStorage,
} from "../src/uploads/storage.js";
import { bearer, testApp, USERS } from "./helpers.js";
import { MemoryUploadConnectionStore } from "./memory-upload-store.js";

class MemoryStorage implements UploadStorage {
  readonly bucket = "firra-staging-raw";
  sizes = new Map<string, number>();
  created: { objectName: string; size: number }[] = [];

  createUpload(objectName: string, size: number) {
    this.created.push({ objectName, size });
    return Promise.resolve(`https://storage.googleapis.com/upload/session/${objectName}`);
  }

  objectSize(objectName: string) {
    return Promise.resolve(this.sizes.get(objectName) ?? null);
  }
}

function setup(uploads: UploadStorage | null = new MemoryStorage()) {
  const uploadConnections = new MemoryUploadConnectionStore();
  const { app, repo, queue } = testApp({ uploads, uploadConnections });
  const workspaceId = randomUUID();
  const owner = USERS["token-owner"];
  if (!owner) throw new Error("fixture");
  repo.addMember(workspaceId, owner.id, "owner");
  const post = (path: string, body: unknown, token = "token-owner") =>
    app.request(`/v1/workspaces/${workspaceId}/uploads${path}`, {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify(body),
    });
  return { app, post, queue, workspaceId, uploads, uploadConnections };
}

describe("mail export uploads", () => {
  it("hands out an upload URL for an id-only object name in the member's upload connection", async () => {
    const storage = new MemoryStorage();
    const { post, workspaceId, uploadConnections } = setup(storage);
    const response = await post("", { size: 1234, format: "mbox" });
    expect(response.status).toBe(200);
    const body = (await response.json()) as { upload_url: string; object: string };
    const [connectionId] = [...uploadConnections.connections.keys()];
    expect(body.object).toMatch(
      new RegExp(`^uploads/${workspaceId}/${connectionId ?? "x"}/[0-9a-f-]{36}\\.mbox$`),
    );
    expect(storage.created).toEqual([{ objectName: body.object, size: 1234 }]);
    // A second upload reuses the same connection.
    await post("", { size: 10, format: "eml" });
    expect(uploadConnections.connections.size).toBe(1);
  });

  it("queues ingest.upload once the object exists", async () => {
    const storage = new MemoryStorage();
    const { post, queue } = setup(storage);
    const { object } = (await (await post("", { size: 5, format: "eml" })).json()) as {
      object: string;
    };
    expect((await post("/complete", { object, format: "eml" })).status).toBe(409);
    storage.sizes.set(object, 5);
    expect((await post("/complete", { object, format: "eml" })).status).toBe(202);
    expect(queue.tasks.at(-1)?.envelope).toMatchObject({
      type: "ingest.upload",
      payload: { object_uri: `gs://firra-staging-raw/${object}`, format: "eml" },
    });
  });

  it.each([
    ["too large", "", { size: MAX_UPLOAD_BYTES + 1, format: "mbox" }, 400],
    ["unknown format", "", { size: 10, format: "pst" }, 400],
    [
      "foreign object",
      "/complete",
      { object: `uploads/${randomUUID()}/${randomUUID()}/${randomUUID()}.eml`, format: "eml" },
      400,
    ],
    ["path traversal", "/complete", { object: "uploads/../../etc/passwd", format: "eml" }, 400],
  ])("rejects %s", async (_name, path, body, status) => {
    const { post } = setup();
    expect((await post(path, body)).status).toBe(status);
  });

  it("is for owners and admins only and reports when storage is missing", async () => {
    expect((await setup().post("", { size: 1, format: "eml" }, "token-outsider")).status).toBe(403);
    expect((await setup(null).post("", { size: 1, format: "eml" })).status).toBe(503);
  });
});

describe("LocalUploadStorage", () => {
  it("stores exactly the announced bytes once per token under the raw directory", async () => {
    const root = await mkdtemp(join(tmpdir(), "firra-uploads-"));
    const storage = new LocalUploadStorage(root, "http://api.local");
    const object = `uploads/${randomUUID()}/${randomUUID()}/${randomUUID()}.eml`;
    const url = await storage.createUpload(object, 3);
    const token = url.split("/").at(-1) ?? "";
    expect(url.startsWith("http://api.local/local-uploads/")).toBe(true);
    expect(await storage.receive(token, new Uint8Array([1, 2]))).toBe(false);
    expect(await storage.receive(token, new Uint8Array([1, 2, 3]))).toBe(true);
    expect(await storage.receive(token, new Uint8Array([1, 2, 3]))).toBe(false);
    expect(await storage.objectSize(object)).toBe(3);
    expect([...(await readFile(join(root, object)))]).toEqual([1, 2, 3]);
    await expect(storage.createUpload("uploads/../x.eml", 1)).rejects.toThrow("invalid");
  });
});

describe("GcsUploadStorage", () => {
  it("starts a resumable session that never overwrites and returns the session URL", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const fetchImpl = ((url: string | URL, init?: RequestInit) => {
      calls.push({ url: url.toString(), init });
      return Promise.resolve(
        new Response(null, {
          status: 200,
          headers: {
            location: "https://storage.googleapis.com/upload/storage/v1/b/b/o?upload_id=x",
          },
        }),
      );
    }) as typeof fetch;
    const storage = new GcsUploadStorage("firra-staging-raw", fetchImpl);
    Object.defineProperty(storage, "token", { value: () => Promise.resolve("t") });
    const object = `uploads/${randomUUID()}/${randomUUID()}/${randomUUID()}.mbox`;
    const url = await storage.createUpload(object, 42);
    expect(url).toContain("upload_id=x");
    const init = new URL(calls[0]?.url ?? "");
    expect(init.searchParams.get("uploadType")).toBe("resumable");
    expect(init.searchParams.get("ifGenerationMatch")).toBe("0");
    const headers = new Headers(calls[0]?.init?.headers);
    expect(headers.get("x-upload-content-length")).toBe("42");
    expect(headers.get("origin")).toBe(UPLOAD_ORIGIN);
  });
});

it("local uploads omit CORS without WEB_URL and preserve configured origins", async () => {
  const root = await mkdtemp(join(tmpdir(), "firra-no-web-uploads-"));
  const storage = new LocalUploadStorage(root, "http://api.local");
  for (const webUrl of [undefined, "https://app.example"]) {
    const { app } = testApp({ uploads: storage, webUrl });
    const url = await storage.createUpload(
      `uploads/${randomUUID()}/${randomUUID()}/${randomUUID()}.eml`,
      3,
    );
    const response = await app.request(new URL(url).pathname, {
      method: "PUT",
      headers: { origin: "https://app.example" },
      body: new Uint8Array([1, 2, 3]),
    });
    expect(response.status).toBe(200);
    expect(response.headers.get("access-control-allow-origin")).toBe(webUrl ?? null);
  }
});

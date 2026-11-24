import { createHash, randomUUID } from "node:crypto";
import { describe, expect, it } from "vitest";
import { gmailPushProvider } from "../src/webhooks/push.js";
import { InMemoryRepository, RecordingQueue, testApp } from "./helpers.js";
import { MemoryPushStore } from "./memory-push-store.js";
import { TaskService } from "../src/tasks/enqueue.js";

const WS = randomUUID();
const CONN = randomUUID();
const CLIENT_STATE = "s".repeat(64);
const sha = (v: string) => createHash("sha256").update(v).digest("hex");
const NOW = 1_800_000_000_000;

function microsoftApp(status = "active") {
  const store = new MemoryPushStore();
  store.connections.set(`${WS}/${CONN}`, { status, clientStateSha256: sha(CLIENT_STATE) });
  const { app, queue } = testApp({ pushStore: store });
  const post = (query: string, body?: unknown, contentType = "application/json") =>
    app.request(`/webhooks/microsoft?${query}`, {
      method: "POST",
      headers: { "Content-Type": contentType },
      body: body === undefined ? "" : JSON.stringify(body),
    });
  return { post, queue };
}

describe("Microsoft Graph notifications", () => {
  it("answers the validation handshake with the token as plain text", async () => {
    const { post } = microsoftApp();
    const token =
      "Validation: Testing client application reachability for subscription Request-Id: 1";
    const response = await post(
      `w=${WS}&c=${CONN}&validationToken=${encodeURIComponent(token)}`,
      undefined,
      "text/plain; charset=utf-8",
    );
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toMatch(/^text\/plain/);
    expect(await response.text()).toBe(token);
  });

  it("wakes the connection's incremental sync once per minute for a valid clientState", async () => {
    const { post, queue } = microsoftApp();
    const body = { value: [{ clientState: CLIENT_STATE, changeType: "created" }] };
    expect((await post(`w=${WS}&c=${CONN}`, body)).status).toBe(202);
    expect((await post(`w=${WS}&c=${CONN}`, body)).status).toBe(202);
    // Same job and Cloud Tasks name: the queue de-duplicates the second delivery.
    expect(new Set(queue.tasks.map((t) => t.taskId)).size).toBe(1);
    expect(queue.tasks[0]?.envelope).toMatchObject({
      workspace_id: WS,
      type: "sync.incremental",
      payload: { connection_id: CONN, renew_push: false },
    });
  });

  it("asks for a subscription renewal on lifecycle notifications", async () => {
    const { post, queue } = microsoftApp();
    const body = {
      value: [{ clientState: CLIENT_STATE, lifecycleEvent: "reauthorizationRequired" }],
    };
    expect((await post(`w=${WS}&c=${CONN}`, body)).status).toBe(202);
    expect(queue.tasks[0]?.envelope.payload).toMatchObject({ renew_push: true });
  });

  it.each([
    ["a wrong clientState", `w=${WS}&c=${CONN}`, { value: [{ clientState: "x".repeat(64) }] }, 401],
    ["a missing clientState", `w=${WS}&c=${CONN}`, { value: [{}] }, 401],
    [
      "an unknown connection",
      `w=${WS}&c=${randomUUID()}`,
      { value: [{ clientState: CLIENT_STATE }] },
      401,
    ],
    ["missing routing ids", "w=nope", { value: [{ clientState: CLIENT_STATE }] }, 400],
  ])("rejects %s without enqueuing", async (_name, query, body, status) => {
    const { post, queue } = microsoftApp();
    expect((await post(query, body)).status).toBe(status);
    expect(queue.tasks).toHaveLength(0);
  });

  it("acknowledges but ignores notifications for revoked connections", async () => {
    const { post, queue } = microsoftApp("revoked");
    const body = { value: [{ clientState: CLIENT_STATE }] };
    expect((await post(`w=${WS}&c=${CONN}`, body)).status).toBe(202);
    expect(queue.tasks).toHaveLength(0);
  });
});

describe("Gmail push (Pub/Sub)", () => {
  function gmailApp() {
    const store = new MemoryPushStore();
    store.gmail.set("ana@northbeam.agency", [
      { workspaceId: WS, connectionId: CONN },
      { workspaceId: randomUUID(), connectionId: randomUUID() },
    ]);
    const queue = new RecordingQueue();
    const tasks = new TaskService(new InMemoryRepository(), queue);
    const provider = gmailPushProvider({
      tasks,
      store,
      now: () => NOW,
      verifyIdToken: (token) => Promise.resolve(token === "pubsub-token" ? "push@p.iam" : null),
    });
    const { app } = testApp({ webhooks: [provider], tasks });
    const post = (data: unknown, token = "pubsub-token") =>
      app.request("/webhooks/gmail", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({
          message: { data: Buffer.from(JSON.stringify(data)).toString("base64"), messageId: "1" },
          subscription: "projects/p/subscriptions/gmail-push",
        }),
      });
    return { post, queue };
  }

  it("wakes every active connection of the mailbox", async () => {
    const { post, queue } = gmailApp();
    const response = await post({ emailAddress: "Ana@Northbeam.agency", historyId: 12345 });
    expect(response.status).toBe(200);
    expect(queue.tasks.map((t) => t.envelope.type)).toEqual([
      "sync.incremental",
      "sync.incremental",
    ]);
    expect(queue.tasks[0]?.envelope.idempotency_key).toBe(`push:${CONN}:${String(NOW / 60_000)}`);
  });

  it("rejects pushes without a valid Pub/Sub token", async () => {
    const { post, queue } = gmailApp();
    expect((await post({ emailAddress: "ana@northbeam.agency" }, "forged")).status).toBe(401);
    expect(queue.tasks).toHaveLength(0);
  });

  it("acknowledges malformed or unknown messages without work", async () => {
    const { post, queue } = gmailApp();
    expect((await post({ nothing: true })).status).toBe(200);
    expect((await post({ emailAddress: "other@example.com", historyId: 1 })).status).toBe(200);
    expect(queue.tasks).toHaveLength(0);
  });
});

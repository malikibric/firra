import { randomUUID } from "node:crypto";
import { describe, expect, it } from "vitest";
import type { ReadCaller } from "../src/lib/read-store.js";
import type { SetupStore } from "../src/lib/setup-store.js";
import { bearer, testApp, USERS } from "./helpers.js";
import { ID, WS } from "./setup-fixtures.js";

class Store implements SetupStore {
  connections = [
    { id: randomUUID(), backfill_months: 3 },
    { id: randomUUID(), backfill_months: 12 },
  ];
  fail = false;
  policy: string | null = "metadata_only";
  writes: string[] = [];
  lookups: { caller: ReadCaller; workspaceId: string }[] = [];
  write(_c: ReadCaller, _w: string | null, operation: string) {
    this.writes.push(operation);
    return Promise.resolve(undefined);
  }
  read() {
    return Promise.resolve({});
  }
  clientPolicy() {
    return Promise.resolve(this.policy);
  }
  mailConnections(caller: ReadCaller, workspaceId: string) {
    this.lookups.push({ caller, workspaceId });
    return this.fail ? Promise.reject(new Error("down")) : Promise.resolve(this.connections);
  }
}

function setup() {
  const store = new Store();
  const t = testApp({ setupStore: store });
  t.repo.addMember(WS, USERS["token-owner"]?.id ?? "", "owner");
  return { ...t, store };
}
function putPolicy(app: ReturnType<typeof testApp>["app"], policy: string) {
  return app.request(`/v1/workspaces/${WS}/clients/${ID}/policy`, {
    method: "PUT",
    headers: { ...bearer("token-owner"), "content-type": "application/json" },
    body: JSON.stringify({ policy }),
  });
}

describe("a client becoming Full reads its mail again", () => {
  it("queues the monthly windows of every active mailbox with fresh task names", async () => {
    const { app, queue, store } = setup();
    expect((await putPolicy(app, "full")).status).toBe(204);
    expect(store.lookups[0]?.workspaceId).toBe(WS);
    const tasks = queue.tasks.filter((t) => t.envelope.type === "sync.backfill_chunk");
    expect(tasks).toHaveLength(3 + 12);
    expect(new Set(tasks.map((t) => t.taskId)).size).toBe(15);
    expect(new Set(tasks.map((t) => t.envelope.workspace_id))).toEqual(new Set([WS]));
    for (const t of tasks) expect(t.queue).toBe("sync");
  });

  it("does not queue anything for metadata_only or excluded", async () => {
    const { app, queue, store } = setup();
    expect((await putPolicy(app, "metadata_only")).status).toBe(204);
    expect((await putPolicy(app, "excluded")).status).toBe(422); // exclusion needs confirmation
    expect(store.lookups).toHaveLength(0);
    expect(queue.tasks).toHaveLength(0);
  });

  it("never fails or hides the policy change when queueing is impossible", async () => {
    const { app, queue, store } = setup();
    store.fail = true;
    expect((await putPolicy(app, "full")).status).toBe(204);
    expect(store.writes).toEqual(["setPolicy"]);
    expect(queue.tasks).toHaveLength(0);
  });

  it("queues nothing when the client already was Full", async () => {
    const { app, queue, store } = setup();
    store.policy = "full";
    expect((await putPolicy(app, "full")).status).toBe(204);
    expect(store.lookups).toHaveLength(0);
    expect(queue.tasks).toHaveLength(0);
  });

  it("queues nothing for a client the caller cannot see", async () => {
    const { app, queue, store } = setup();
    store.policy = null;
    await putPolicy(app, "full");
    expect(queue.tasks).toHaveLength(0);
  });

  it("coalesces re-reads of several clients in the same hour into the same tasks", async () => {
    const { app, queue } = setup();
    await putPolicy(app, "full");
    const first = new Set(queue.tasks.map((t) => t.taskId));
    await app.request(`/v1/workspaces/${WS}/clients/${ID.replace(/2/g, "3")}/policy`, {
      method: "PUT",
      headers: { ...bearer("token-owner"), "content-type": "application/json" },
      body: JSON.stringify({ policy: "full" }),
    });
    expect(new Set(queue.tasks.map((t) => t.taskId))).toEqual(first);
  });
});

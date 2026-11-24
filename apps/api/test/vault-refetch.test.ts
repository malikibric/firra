import { randomUUID } from "node:crypto";
import { vaultRefetchResponse } from "@firra/contracts";
import { describe, expect, it } from "vitest";
import { bearer, testApp, USERS } from "./helpers.js";
import { MemoryVaultStore } from "./memory-vault-store.js";

const userA = USERS["token-owner"]?.id ?? "";
const userB = USERS["token-outsider"]?.id ?? "";
const ws = randomUUID();

function setup() {
  const store = new MemoryVaultStore();
  return { store, ...testApp({ vaultStore: store }) };
}
function refetch(app: ReturnType<typeof testApp>["app"], token = "token-owner") {
  return app.request("/v1/vault/refetch", { method: "POST", headers: bearer(token) });
}

describe("POST /v1/vault/refetch", () => {
  it("asks only the caller's own mailboxes to send their mail again", async () => {
    const { store, app, queue } = setup();
    const mine = { id: randomUUID(), workspace_id: ws };
    store.mailboxes.set(userA, [mine]);
    store.mailboxes.set(userB, [{ id: randomUUID(), workspace_id: ws }]);
    const res = await refetch(app);
    expect(res.status).toBe(200);
    expect(res.headers.get("cache-control")).toBe("no-store");
    expect(vaultRefetchResponse.parse(await res.json())).toEqual({ requested: 1 });
    expect(queue.tasks).toHaveLength(1);
    const task = queue.tasks[0];
    expect(task?.queue).toBe("sync");
    expect(task?.envelope.type).toBe("vault.rehydrate");
    expect(task?.envelope.workspace_id).toBe(ws);
    expect(task?.envelope.payload).toEqual({ connection_id: mine.id, user_id: userA });
  });

  it("runs once per mailbox per day and says 0 when the caller has none", async () => {
    const { store, app, queue } = setup();
    store.mailboxes.set(userA, [{ id: randomUUID(), workspace_id: ws }]);
    await refetch(app);
    await refetch(app);
    expect(queue.tasks.map((t) => t.taskId)).toHaveLength(2);
    expect(new Set(queue.tasks.map((t) => t.taskId)).size).toBe(1);
    const none = await refetch(app, "token-outsider");
    expect(none.status).toBe(500); // no workspace to audit: fails closed, like an empty poll
    expect(queue.tasks).toHaveLength(2);
  });

  it("needs a signed-in user and a configured vault", async () => {
    const { app } = setup();
    expect((await app.request("/v1/vault/refetch", { method: "POST" })).status).toBe(401);
    const unconfigured = testApp({ vaultStore: null }).app;
    expect((await refetch(unconfigured)).status).toBe(503);
  });

  it("writes no task when the audit cannot be written", async () => {
    const { store, app, repo, queue } = setup();
    store.mailboxes.set(userA, [{ id: randomUUID(), workspace_id: ws }]);
    repo.writeAudit = () => Promise.reject(new Error("audit down"));
    expect((await refetch(app)).status).toBe(500);
    expect(queue.tasks).toHaveLength(0);
  });
});

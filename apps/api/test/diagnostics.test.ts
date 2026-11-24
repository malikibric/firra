import { randomUUID } from "node:crypto";
import { beforeEach, describe, expect, it } from "vitest";
import { bearer, INTERNAL_TOKEN, testApp, USERS } from "./helpers.js";

const owner = USERS["token-owner"];
const admin = USERS["token-admin"];

describe("user diagnostics routes", () => {
  let ctx: ReturnType<typeof testApp>;
  const workspaceId = randomUUID();

  beforeEach(() => {
    ctx = testApp();
    if (!owner || !admin) throw new Error("fixtures");
    ctx.repo.addMember(workspaceId, owner.id, "owner");
    ctx.repo.addMember(workspaceId, admin.id, "admin");
  });

  const ping = (token?: string, headers: Record<string, string> = {}) =>
    ctx.app.request(`/v1/workspaces/${workspaceId}/diagnostics/ping`, {
      method: "POST",
      headers: { ...(token ? bearer(token) : {}), ...headers },
    });

  it("rejects missing or invalid tokens", async () => {
    expect((await ping()).status).toBe(401);
    expect((await ping("forged")).status).toBe(401);
  });

  it("forbids users who are not members (no workspace existence leak)", async () => {
    expect((await ping("token-outsider")).status).toBe(403);
    const unknown = await ctx.app.request(`/v1/workspaces/${randomUUID()}/diagnostics/ping`, {
      method: "POST",
      headers: bearer("token-owner"),
    });
    expect(unknown.status).toBe(403);
    const malformed = await ctx.app.request("/v1/workspaces/not-a-uuid/diagnostics/ping", {
      method: "POST",
      headers: bearer("token-owner"),
    });
    expect(malformed.status).toBe(403);
  });

  it("enqueues a ping for owners and admins", async () => {
    for (const token of ["token-owner", "token-admin"]) {
      const response = await ping(token);
      expect(response.status).toBe(202);
    }
    expect(ctx.queue.tasks).toHaveLength(2);
    const task = ctx.queue.tasks[0];
    expect(task?.queue).toBe("process");
    expect(task?.envelope).toMatchObject({ workspace_id: workspaceId, type: "diagnostics.ping" });
  });

  it("honours Idempotency-Key", async () => {
    const first = (await (await ping("token-owner", { "Idempotency-Key": "abc" })).json()) as {
      id: string;
    };
    const second = (await (await ping("token-owner", { "Idempotency-Key": "abc" })).json()) as {
      id: string;
    };
    expect(second.id).toBe(first.id);
    expect(ctx.repo.jobs).toHaveLength(1);
    expect((await ping("token-owner", { "Idempotency-Key": "bad key!" })).status).toBe(400);
  });

  it("returns job status to members of the same workspace only", async () => {
    const job = (await (await ping("token-owner")).json()) as { id: string };
    const own = await ctx.app.request(`/v1/workspaces/${workspaceId}/jobs/${job.id}`, {
      headers: bearer("token-admin"),
    });
    expect(own.status).toBe(200);
    expect(await own.json()).toMatchObject({ id: job.id, status: "queued" });

    const otherWorkspace = randomUUID();
    if (!owner) throw new Error("fixtures");
    ctx.repo.addMember(otherWorkspace, owner.id, "owner");
    const cross = await ctx.app.request(`/v1/workspaces/${otherWorkspace}/jobs/${job.id}`, {
      headers: bearer("token-owner"),
    });
    expect(cross.status).toBe(404);
  });
});

describe("internal diagnostics routes", () => {
  it("require an allowlisted Google ID token", async () => {
    const { app } = testApp();
    const response = await app.request("/internal/v1/diagnostics/ping", {
      method: "POST",
      headers: bearer("token-owner"),
      body: JSON.stringify({ workspace_id: randomUUID() }),
    });
    expect(response.status).toBe(401);
  });

  it("enqueue a ping for an existing workspace and report job status", async () => {
    const { app, repo, queue } = testApp();
    const workspaceId = randomUUID();
    repo.workspaces.add(workspaceId);

    const missing = await app.request("/internal/v1/diagnostics/ping", {
      method: "POST",
      headers: bearer(INTERNAL_TOKEN),
      body: JSON.stringify({ workspace_id: randomUUID() }),
    });
    expect(missing.status).toBe(404);

    const response = await app.request("/internal/v1/diagnostics/ping", {
      method: "POST",
      headers: bearer(INTERNAL_TOKEN),
      body: JSON.stringify({ workspace_id: workspaceId }),
    });
    expect(response.status).toBe(202);
    expect(queue.tasks).toHaveLength(1);
    const job = (await response.json()) as { id: string };

    const status = await app.request(`/internal/v1/workspaces/${workspaceId}/jobs/${job.id}`, {
      headers: bearer(INTERNAL_TOKEN),
    });
    expect(await status.json()).toMatchObject({ id: job.id, status: "queued" });
  });
});

describe("internal llm probe", () => {
  it("enqueues diagnostics.llm_probe on the extract queue", async () => {
    const { app, repo, queue } = testApp();
    const workspaceId = randomUUID();
    repo.workspaces.add(workspaceId);
    const response = await app.request("/internal/v1/diagnostics/llm-probe", {
      method: "POST",
      headers: bearer(INTERNAL_TOKEN),
      body: JSON.stringify({ workspace_id: workspaceId }),
    });
    expect(response.status).toBe(202);
    expect(queue.tasks).toHaveLength(1);
    expect(queue.tasks[0]).toMatchObject({
      queue: "extract",
      envelope: { workspace_id: workspaceId, type: "diagnostics.llm_probe" },
    });
    const unauthorized = await app.request("/internal/v1/diagnostics/llm-probe", {
      method: "POST",
      headers: bearer("token-owner"),
      body: JSON.stringify({ workspace_id: workspaceId }),
    });
    expect(unauthorized.status).toBe(401);
  });
});

describe("app basics", () => {
  it("serves health, readiness and security headers", async () => {
    const { app } = testApp({ checkReady: () => Promise.reject(new Error("down")) });
    const health = await app.request("/health");
    expect(health.status).toBe(200);
    expect(health.headers.get("x-content-type-options")).toBe("nosniff");
    expect((await app.request("/ready")).status).toBe(503);
    expect((await app.request("/nope")).status).toBe(404);
  });

  it("logs route patterns, not raw URLs", async () => {
    const { app, logs } = testApp();
    const id = randomUUID();
    await app.request(`/v1/workspaces/${id}/diagnostics/ping`, { method: "POST" });
    const entry = JSON.parse(logs.at(-1) ?? "{}") as Record<string, unknown>;
    expect(entry.message).toBe("http.request");
    expect(JSON.stringify(entry)).not.toContain(id);
  });

  it("hides unhandled errors", async () => {
    const captured: unknown[] = [];
    const { app, repo } = testApp({ captureException: (e) => captured.push(e) });
    repo.getMembershipRole = () => Promise.reject(new Error("db exploded: secret detail"));
    const response = await app.request(`/v1/workspaces/${randomUUID()}/diagnostics/ping`, {
      method: "POST",
      headers: bearer("token-owner"),
    });
    expect(response.status).toBe(500);
    expect(await response.text()).not.toContain("secret detail");
    expect(captured).toHaveLength(1);
  });
});

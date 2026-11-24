import { randomUUID } from "node:crypto";
import { describe, expect, it, vi } from "vitest";
import { CloudTasksQueue } from "../src/tasks/cloud-tasks.js";
import { TaskService, taskIdFor } from "../src/tasks/enqueue.js";
import { signLocalToken } from "../src/tasks/local-dispatcher.js";
import { InMemoryRepository, RecordingQueue } from "./helpers.js";

describe("TaskService", () => {
  const workspaceId = randomUUID();

  it("validates payloads against the contract", async () => {
    const service = new TaskService(new InMemoryRepository(), new RecordingQueue());
    await expect(
      service.enqueue({
        workspaceId,
        type: "diagnostics.ping",
        idempotencyKey: "k",
        payload: { requested_at: "yesterday" },
      }),
    ).rejects.toThrow();
  });

  it("does not re-enqueue finished jobs", async () => {
    const repo = new InMemoryRepository();
    const queue = new RecordingQueue();
    const service = new TaskService(repo, queue);
    const request = {
      workspaceId,
      type: "diagnostics.ping" as const,
      idempotencyKey: "k",
      payload: { requested_at: new Date().toISOString() },
    };
    const job = await service.enqueue(request);
    job.status = "succeeded";
    await service.enqueue(request);
    expect(queue.tasks).toHaveLength(1);
    expect(queue.tasks[0]?.taskId).toBe(taskIdFor(workspaceId, "k"));
  });

  it("re-runs a failed job only when asked, under a fresh task name", async () => {
    const repo = new InMemoryRepository();
    const queue = new RecordingQueue();
    const service = new TaskService(repo, queue);
    const request = {
      workspaceId,
      type: "diagnostics.ping" as const,
      idempotencyKey: "failed-k",
      payload: { requested_at: new Date().toISOString() },
    };
    const job = await service.enqueue(request);
    Object.assign(job, { status: "failed", attempts: 5, errorClass: "provider" });

    await service.enqueue(request);
    expect(queue.tasks).toHaveLength(1);

    const retried = await service.enqueue({ ...request, retryFailed: true });
    expect(retried).toMatchObject({ id: job.id, status: "queued", attempts: 0, errorClass: null });
    expect(queue.tasks).toHaveLength(2);
    expect(queue.tasks[1]?.taskId).not.toBe(taskIdFor(workspaceId, "failed-k"));
    expect(queue.tasks[1]?.envelope).toMatchObject({ job_id: job.id, idempotency_key: "failed-k" });
  });

  it("derives stable, workspace-specific task ids", () => {
    expect(taskIdFor(workspaceId, "k")).toBe(taskIdFor(workspaceId, "k"));
    expect(taskIdFor(workspaceId, "k")).not.toBe(taskIdFor(randomUUID(), "k"));
    expect(taskIdFor(workspaceId, "k")).toMatch(/^[0-9a-f]{64}$/);
  });
});

describe("CloudTasksQueue", () => {
  const config = {
    projectId: "firra-staging",
    region: "europe-west3",
    workerUrl: "https://worker.example.run.app",
    invokerServiceAccount: "tasks-invoker@firra-staging.iam.gserviceaccount.com",
  };
  const envelope = {
    workspace_id: randomUUID(),
    job_id: randomUUID(),
    idempotency_key: "k",
    type: "diagnostics.ping",
    payload: { requested_at: "2026-09-27T10:00:00Z" },
  };

  it("creates a named HTTP task with an OIDC token for the worker", async () => {
    const createTask = vi.fn().mockResolvedValue([{}]);
    await new CloudTasksQueue(config, { createTask }).enqueue("process", "abc", envelope);
    const [request] = createTask.mock.calls[0] as [Record<string, unknown>];
    expect(request).toMatchObject({
      parent: "projects/firra-staging/locations/europe-west3/queues/process",
      task: {
        name: "projects/firra-staging/locations/europe-west3/queues/process/tasks/abc",
        httpRequest: {
          httpMethod: "POST",
          url: "https://worker.example.run.app/tasks/diagnostics.ping",
          oidcToken: {
            serviceAccountEmail: config.invokerServiceAccount,
            audience: config.workerUrl,
          },
        },
      },
    });
  });

  it("treats ALREADY_EXISTS as success and rethrows other errors", async () => {
    const duplicate = vi.fn().mockRejectedValue(Object.assign(new Error("exists"), { code: 6 }));
    await expect(
      new CloudTasksQueue(config, { createTask: duplicate }).enqueue("process", "a", envelope),
    ).resolves.toBeUndefined();
    const denied = vi.fn().mockRejectedValue(Object.assign(new Error("denied"), { code: 7 }));
    await expect(
      new CloudTasksQueue(config, { createTask: denied }).enqueue("process", "a", envelope),
    ).rejects.toThrow("denied");
  });
});

describe("signLocalToken", () => {
  it("produces a short-lived HS256 JWT for the local worker", () => {
    const [header, claims] = signLocalToken("x".repeat(32), 1000).split(".");
    expect(JSON.parse(Buffer.from(header ?? "", "base64url").toString())).toEqual({
      alg: "HS256",
      typ: "JWT",
    });
    expect(JSON.parse(Buffer.from(claims ?? "", "base64url").toString())).toEqual({
      iss: "firra-local-dispatcher",
      aud: "firra-worker-local",
      iat: 1000,
      exp: 1060,
    });
  });
});

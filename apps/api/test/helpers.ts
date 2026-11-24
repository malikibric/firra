import { randomUUID } from "node:crypto";
import type { MemberRole, TaskEnvelope } from "@firra/contracts";
import { createApp, type AppDeps } from "../src/app.js";
import { createLogger } from "../src/lib/logger.js";
import type { ApiRepository, AuditEntry, JobRecord, NewJob } from "../src/lib/repository.js";
import type { ReadStore } from "../src/lib/read-store.js";
import type { McpTokenAdmin } from "../src/mcp/admin.js";
import type { MeStore } from "../src/lib/me-store.js";
import type { McpTokenStore, SessionMinter } from "../src/mcp/tokens.js";
import type { AuthUser } from "../src/lib/types.js";
import { OrgService } from "../src/connect/org.js";
import { ConnectService } from "../src/connect/service.js";
import { LocalKek } from "../src/crypto/kek.js";
import { Keyring } from "../src/crypto/keyring.js";
import { TaskService } from "../src/tasks/enqueue.js";
import { MemoryConnectStore } from "./memory-connect-store.js";
import { MemoryPushStore } from "./memory-push-store.js";
import { MemoryUploadConnectionStore } from "./memory-upload-store.js";
import type { QueueName, TaskQueue } from "../src/tasks/queue.js";

export class InMemoryRepository implements ApiRepository {
  memberships = new Map<string, MemberRole>();
  workspaces = new Set<string>();
  jobs: JobRecord[] = [];
  audit: AuditEntry[] = [];
  failAudit = false;

  writeAudit(entry: AuditEntry): Promise<void> {
    if (this.failAudit) return Promise.reject(new Error("audit write failed"));
    this.audit.push(entry);
    return Promise.resolve();
  }

  addMember(workspaceId: string, userId: string, role: MemberRole): void {
    this.workspaces.add(workspaceId);
    this.memberships.set(`${workspaceId}:${userId}`, role);
  }

  getMembershipRole(workspaceId: string, userId: string): Promise<MemberRole | null> {
    return Promise.resolve(this.memberships.get(`${workspaceId}:${userId}`) ?? null);
  }

  workspaceExists(workspaceId: string): Promise<boolean> {
    return Promise.resolve(this.workspaces.has(workspaceId));
  }

  upsertJob(job: NewJob): Promise<JobRecord> {
    const existing = this.jobs.find(
      (j) => j.workspaceId === job.workspaceId && j.idempotencyKey === job.idempotencyKey,
    );
    if (existing) return Promise.resolve(existing);
    const record: JobRecord = {
      id: randomUUID(),
      workspaceId: job.workspaceId,
      type: job.type,
      status: "queued",
      idempotencyKey: job.idempotencyKey,
      attempts: 0,
      progress: {},
      errorClass: null,
      createdAt: new Date().toISOString(),
      finishedAt: null,
    };
    this.jobs.push(record);
    return Promise.resolve(record);
  }

  getJob(workspaceId: string, jobId: string): Promise<JobRecord | null> {
    return Promise.resolve(
      this.jobs.find((j) => j.workspaceId === workspaceId && j.id === jobId) ?? null,
    );
  }

  retryFailedJob(workspaceId: string, jobId: string): Promise<JobRecord | null> {
    const job = this.jobs.find(
      (j) => j.workspaceId === workspaceId && j.id === jobId && j.status === "failed",
    );
    if (!job) return Promise.resolve(null);
    Object.assign(job, { status: "queued", attempts: 0, errorClass: null, finishedAt: null });
    return Promise.resolve(job);
  }
}

export class RecordingQueue implements TaskQueue {
  tasks: { queue: QueueName; taskId: string; envelope: TaskEnvelope }[] = [];

  enqueue(queue: QueueName, taskId: string, envelope: TaskEnvelope): Promise<void> {
    this.tasks.push({ queue, taskId, envelope });
    return Promise.resolve();
  }
}

export const USERS: Record<string, AuthUser> = {
  "token-owner": { id: randomUUID(), email: "owner@example.com" },
  "token-admin": { id: randomUUID(), email: "admin@example.com" },
  "token-outsider": { id: randomUUID(), email: "outsider@example.com" },
};
export const INTERNAL_TOKEN = "google-id-token";
export const SMOKE_SA = "deployer@firra-staging.iam.gserviceaccount.com";

export function testApp(overrides: Partial<AppDeps> = {}) {
  const repo = new InMemoryRepository();
  const queue = new RecordingQueue();
  const logs: string[] = [];
  const logger = createLogger((line) => logs.push(line));
  const tasks = new TaskService(repo, queue);
  const connectStore = new MemoryConnectStore();
  const keyring = new Keyring(new LocalKek(Buffer.alloc(32, 1)), connectStore);
  const deps: AppDeps = {
    logger,
    repo,
    tasks,
    connect: new ConnectService({
      store: connectStore,
      keyring,
      tasks,
      logger,
      google: null,
    }),
    org: new OrgService({
      store: connectStore,
      keyring,
      tasks,
      logger,
      googleDelegationClientId: null,
      microsoft: null,
      microsoftAdminRedirectUri: "https://api.example/oauth/microsoft/admin-callback",
    }),
    webUrl: "https://app.example",
    verifyUser: (token) => Promise.resolve(USERS[token] ?? null),
    verifyInternal: (token) => Promise.resolve(token === INTERNAL_TOKEN ? SMOKE_SA : null),
    webhooks: [],
    checkReady: () => Promise.resolve(true),
    captureException: () => undefined,
    rateLimitPerMinute: 1000,
    pushStore: new MemoryPushStore(),
    uploads: null,
    uploadConnections: new MemoryUploadConnectionStore(),
    readStore: null as ReadStore | null,
    mcpTokens: null as McpTokenAdmin | null,
    vaultStore: null,
    setupStore: null,
    meStore: null as MeStore | null,
    mcpTokenStore: null as McpTokenStore | null,
    sessionMinter: null as SessionMinter | null,
    mcpAllowedOrigins: [] as string[],
    mcpRateLimitPerMinute: 1000,
    userRateLimitPerMinute: 1000,
    release: "test",
    ...overrides,
  };
  return { app: createApp(deps), repo, queue, logs };
}

export function bearer(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` };
}

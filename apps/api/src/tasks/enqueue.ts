import { createHash, randomUUID } from "node:crypto";
import { taskPayloadSchemas, type TaskPayloadInput, type TaskType } from "@firra/contracts";
import type { ApiRepository, JobRecord } from "../lib/repository.js";
import type { QueueName, TaskQueue } from "./queue.js";

const QUEUE_FOR_TASK: Record<TaskType, QueueName> = {
  "diagnostics.ping": "process",
  "diagnostics.llm_probe": "extract",
  "sync.backfill_chunk": "sync",
  "sync.incremental": "sync",
  "ingest.upload": "sync",
  "process.records": "extract",
  "signals.evaluate": "process",
  "memory.rebuild": "process",
  "needs.update": "extract",
  "assessment.replay": "process",
  "synth.cards": "synth",
  "vault.rehydrate": "sync",
  "brief.meetings": "synth",
  "routines.run": "process",
};

export interface EnqueueRequest<T extends TaskType> {
  workspaceId: string;
  type: T;
  idempotencyKey: string;
  payload: TaskPayloadInput<T>;
  /** Links the job to a connection (progress per connection in the UI). */
  connectionId?: string;
  /** Re-runs the job if it already failed (e.g. a backfill month, when the user reconnects). */
  retryFailed?: boolean;
}

/** Deterministic, non-sequential task id so retries of the same enqueue are de-duplicated. */
export function taskIdFor(workspaceId: string, idempotencyKey: string): string {
  return createHash("sha256").update(`${workspaceId}:${idempotencyKey}`).digest("hex");
}

export class TaskService {
  constructor(
    private readonly repo: ApiRepository,
    private readonly queue: TaskQueue,
  ) {}

  /** Records the job (idempotent) and hands it to the queue unless it already finished. */
  async enqueue<T extends TaskType>(request: EnqueueRequest<T>): Promise<JobRecord> {
    const payload = taskPayloadSchemas[request.type].parse(request.payload) as Record<
      string,
      unknown
    >;
    let job = await this.repo.upsertJob({
      workspaceId: request.workspaceId,
      type: request.type,
      idempotencyKey: request.idempotencyKey,
      payload,
      ...(request.connectionId ? { connectionId: request.connectionId } : {}),
    });
    // Cloud Tasks keeps the names of finished tasks for a while, so a retry needs a fresh name.
    let taskKey = request.idempotencyKey;
    if (job.status === "failed" && request.retryFailed) {
      job = (await this.repo.retryFailedJob(request.workspaceId, job.id)) ?? job;
      taskKey = `${request.idempotencyKey}:retry:${randomUUID()}`;
    }
    if (job.status === "queued" || job.status === "running") {
      await this.queue.enqueue(
        QUEUE_FOR_TASK[request.type],
        taskIdFor(request.workspaceId, taskKey),
        {
          workspace_id: request.workspaceId,
          job_id: job.id,
          idempotency_key: request.idempotencyKey,
          type: request.type,
          payload,
        },
      );
    }
    return job;
  }
}

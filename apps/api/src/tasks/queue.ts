import type { TaskEnvelope } from "@firra/contracts";

/** Cloud Tasks queues, one per pipeline stage (see infra/terraform). */
export const QUEUES = ["sync", "process", "extract", "synth", "external", "brief"] as const;
export type QueueName = (typeof QUEUES)[number];

export interface TaskQueue {
  /** Delivers the envelope to the worker. Must be safe to call twice for the same task id. */
  enqueue(queue: QueueName, taskId: string, envelope: TaskEnvelope): Promise<void>;
}

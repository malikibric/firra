import type { TaskEnvelope } from "@firra/contracts";
import { CloudTasksClient } from "@google-cloud/tasks";
import type { QueueName, TaskQueue } from "./queue.js";

const ALREADY_EXISTS = 6;

export interface CloudTasksConfig {
  projectId: string;
  region: string;
  workerUrl: string;
  invokerServiceAccount: string;
}

/** Creates HTTP tasks that Cloud Tasks delivers to the worker with a Google-signed OIDC token. */
export class CloudTasksQueue implements TaskQueue {
  constructor(
    private readonly config: CloudTasksConfig,
    private readonly client: Pick<CloudTasksClient, "createTask"> = new CloudTasksClient(),
  ) {}

  async enqueue(queue: QueueName, taskId: string, envelope: TaskEnvelope): Promise<void> {
    const parent = `projects/${this.config.projectId}/locations/${this.config.region}/queues/${queue}`;
    try {
      await this.client.createTask({
        parent,
        task: {
          // Named tasks are de-duplicated by Cloud Tasks.
          name: `${parent}/tasks/${taskId}`,
          httpRequest: {
            httpMethod: "POST",
            url: `${this.config.workerUrl}/tasks/${envelope.type}`,
            headers: { "Content-Type": "application/json" },
            body: Buffer.from(JSON.stringify(envelope)).toString("base64"),
            oidcToken: {
              serviceAccountEmail: this.config.invokerServiceAccount,
              audience: this.config.workerUrl,
            },
          },
        },
      });
    } catch (error) {
      if ((error as { code?: unknown }).code === ALREADY_EXISTS) return;
      throw error;
    }
  }
}

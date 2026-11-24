import { createHmac } from "node:crypto";
import type { TaskEnvelope } from "@firra/contracts";
import type { Logger } from "../lib/logger.js";
import type { QueueName, TaskQueue } from "./queue.js";

// Must match services/worker/firra/tasks/auth.py.
const LOCAL_ISSUER = "firra-local-dispatcher";
const LOCAL_AUDIENCE = "firra-worker-local";

function base64url(input: string | Buffer): string {
  return Buffer.from(input).toString("base64url");
}

export function signLocalToken(secret: string, nowSeconds = Math.floor(Date.now() / 1000)): string {
  const header = base64url(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const claims = base64url(
    JSON.stringify({
      iss: LOCAL_ISSUER,
      aud: LOCAL_AUDIENCE,
      iat: nowSeconds,
      exp: nowSeconds + 60,
    }),
  );
  const signature = createHmac("sha256", secret).update(`${header}.${claims}`).digest("base64url");
  return `${header}.${claims}.${signature}`;
}

/** Local stand-in for Cloud Tasks: posts straight to the worker, retrying 5xx a few times. */
export class LocalDispatcher implements TaskQueue {
  constructor(
    private readonly workerUrl: string,
    private readonly secret: string,
    private readonly logger: Logger,
    private readonly maxAttempts = 3,
  ) {}

  async enqueue(queue: QueueName, _taskId: string, envelope: TaskEnvelope): Promise<void> {
    for (let attempt = 1; attempt <= this.maxAttempts; attempt += 1) {
      let response: Response;
      try {
        response = await fetch(`${this.workerUrl}/tasks/${envelope.type}`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${signLocalToken(this.secret)}`,
          },
          body: JSON.stringify(envelope),
          signal: AbortSignal.timeout(30_000),
        });
      } catch {
        // No local worker running: the job row stays queued, as it would behind Cloud Tasks.
        this.logger.warn("local_dispatch.unreachable", { queue, task_type: envelope.type });
        return;
      }
      if (response.ok) return;
      this.logger.warn("local_dispatch.retry", {
        queue,
        task_type: envelope.type,
        status: String(response.status),
        attempt,
      });
      if (response.status < 500) return;
      await new Promise((resolve) => setTimeout(resolve, 200 * attempt));
    }
  }
}

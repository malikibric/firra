import type { TaskService } from "../tasks/enqueue.js";

/** Backfill windows: one per calendar month, newest first, the current month ending now. */
export interface Window {
  start: Date;
  end: Date;
  label: string;
}

export function monthWindows(now: Date, months: number): Window[] {
  const windows: Window[] = [];
  for (let i = 0; i < months; i += 1) {
    const start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - i, 1));
    const end =
      i === 0 ? now : new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - i + 1, 1));
    const label = `${String(start.getUTCFullYear())}-${String(start.getUTCMonth() + 1).padStart(2, "0")}`;
    windows.push({ start, end, label });
  }
  return windows;
}

/**
 * Reads the monthly windows of a connection again (a client's policy became Full, so mail that was
 * kept as metadata only can now be stored with its text). Fresh task names per call, so the earlier,
 * finished backfill does not swallow it; records are upserted by (connection, external id).
 */
export async function restartBackfill(
  tasks: TaskService,
  workspaceId: string,
  connectionId: string,
  now: Date,
  months: number,
  nonce: string,
): Promise<void> {
  for (const window of monthWindows(now, months)) {
    await tasks.enqueue({
      workspaceId,
      connectionId,
      type: "sync.backfill_chunk",
      idempotencyKey: `backfill:${connectionId}:${window.label}:again:${nonce}`,
      retryFailed: true,
      payload: {
        connection_id: connectionId,
        window_start: window.start.toISOString(),
        window_end: window.end.toISOString(),
      },
    });
  }
}

/**
 * Incremental cursor first (so nothing arriving during backfill is missed), then monthly windows.
 * Reconnecting re-runs months that failed; each resumes from its saved page.
 */
export async function startConnectionSync(
  tasks: TaskService,
  workspaceId: string,
  connectionId: string,
  now: Date,
  months = 12,
): Promise<void> {
  await tasks.enqueue({
    workspaceId,
    connectionId,
    type: "sync.incremental",
    idempotencyKey: `incremental-init:${connectionId}`,
    payload: { connection_id: connectionId },
  });
  for (const window of monthWindows(now, months)) {
    await tasks.enqueue({
      workspaceId,
      connectionId,
      type: "sync.backfill_chunk",
      idempotencyKey: `backfill:${connectionId}:${window.label}`,
      retryFailed: true,
      payload: {
        connection_id: connectionId,
        window_start: window.start.toISOString(),
        window_end: window.end.toISOString(),
      },
    });
  }
}

import { randomUUID } from "node:crypto";
import type { UploadConnectionStore } from "../src/routes/uploads.js";

export class MemoryUploadConnectionStore implements UploadConnectionStore {
  connections = new Map<string, { workspaceId: string; userId: string; label: string }>();

  ensureUploadConnection(workspaceId: string, userId: string, label: string) {
    for (const [id, c] of this.connections)
      if (c.workspaceId === workspaceId && c.userId === userId) return Promise.resolve(id);
    const id = randomUUID();
    this.connections.set(id, { workspaceId, userId, label });
    return Promise.resolve(id);
  }

  isUploadConnection(workspaceId: string, connectionId: string) {
    return Promise.resolve(this.connections.get(connectionId)?.workspaceId === workspaceId);
  }
}

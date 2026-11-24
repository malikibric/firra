import type { PushConnection, PushStore } from "../src/webhooks/push.js";

export class MemoryPushStore implements PushStore {
  gmail = new Map<string, { workspaceId: string; connectionId: string }[]>();
  connections = new Map<string, PushConnection>();

  gmailConnectionsForAddress(email: string) {
    return Promise.resolve(this.gmail.get(email) ?? []);
  }

  pushConnection(workspaceId: string, connectionId: string) {
    return Promise.resolve(this.connections.get(`${workspaceId}/${connectionId}`) ?? null);
  }
}

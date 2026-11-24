import { randomUUID } from "node:crypto";
import {
  authorizationUrlResponse,
  claimInviteResponse,
  contributorConnectionsResponse,
  meResponse,
} from "@firra/contracts";
import { describe, expect, it } from "vitest";
import { ConnectService } from "../src/connect/service.js";
import { LocalKek } from "../src/crypto/kek.js";
import { Keyring } from "../src/crypto/keyring.js";
import { createLogger } from "../src/lib/logger.js";
import {
  MeNotFoundError,
  type ContributorConnectionRow,
  type MeData,
  type MeStore,
} from "../src/lib/me-store.js";
import type { ReadCaller } from "../src/lib/read-store.js";
import { TaskService } from "../src/tasks/enqueue.js";
import { InMemoryRepository, RecordingQueue, USERS, bearer, testApp } from "./helpers.js";
import { MemoryConnectStore } from "./memory-connect-store.js";

const WS = "11111111-1111-4111-8111-111111111111";
const CONTRIBUTOR = USERS["token-owner"]?.id ?? "";
const TOKEN = "ab".repeat(32);
const ROW: ContributorConnectionRow = {
  id: "33333333-3333-4333-8333-333333333333",
  provider: "gmail",
  account_label: "ana@example.com",
  status: "active",
  last_synced_at: null,
  last_error_class: null,
};

class FakeMe implements MeStore {
  calls: string[] = [];
  mode: "ok" | "missing" | "boom" = "ok";
  me(caller: ReadCaller): Promise<MeData> {
    this.calls.push(`me ${caller.userId}`);
    return Promise.resolve({
      memberships: [],
      contributions: [
        { workspace_id: WS, workspace_name: "Northbeam", contributor_id: randomUUID() },
      ],
    });
  }
  claim(caller: ReadCaller, request: { token: string }) {
    this.calls.push(`claim ${request.token.slice(0, 4)}`);
    if (this.mode === "missing") return Promise.reject(new MeNotFoundError());
    if (this.mode === "boom") return Promise.reject(new Error("PRIVATE"));
    return Promise.resolve({ workspace_id: WS, contributor_id: randomUUID() });
  }
  connections(_caller: ReadCaller, workspaceId: string) {
    this.calls.push(`connections ${workspaceId}`);
    return Promise.resolve([ROW]);
  }
  disconnect(_caller: ReadCaller, connectionId: string) {
    this.calls.push(`disconnect ${connectionId}`);
    if (this.mode === "missing") return Promise.reject(new MeNotFoundError());
    return Promise.resolve();
  }
}

function setup(configured = false) {
  const store = new MemoryConnectStore();
  const repo = new InMemoryRepository();
  const queue = new RecordingQueue();
  const connect = new ConnectService({
    store,
    keyring: new Keyring(new LocalKek(Buffer.alloc(32, 3)), store),
    tasks: new TaskService(repo, queue),
    google: configured
      ? {
          clientId: "cid",
          clientSecret: "s",
          redirectUri: "https://api.example/oauth/google/callback",
        }
      : null,
    logger: createLogger(() => undefined),
  });
  const me = new FakeMe();
  const t = testApp({ meStore: me, connect });
  return { ...t, me, store };
}

const json = (token: string, body: unknown, method = "POST") => ({
  method,
  headers: { ...bearer(token), "content-type": "application/json" },
  body: JSON.stringify(body),
});

describe("contributor routes", () => {
  it("returns the signed-in user's workspaces without any workspace role", async () => {
    const { app, me } = setup();
    const res = await app.request("/v1/me", { headers: bearer("token-outsider") });
    expect(res.status).toBe(200);
    const body = meResponse.parse(await res.json());
    expect(body.user.email).toBe("outsider@example.com");
    expect(body.contributions[0]?.workspace_name).toBe("Northbeam");
    expect(me.calls[0]).toMatch(/^me /);
    expect((await app.request("/v1/me")).status).toBe(401);
  });

  it("claims an invite as the caller; refusals look the same and bad codes are 422", async () => {
    const { app, me } = setup();
    const ok = await app.request("/v1/contributor/claim", json("token-outsider", { token: TOKEN }));
    expect(ok.status).toBe(200);
    claimInviteResponse.parse(await ok.json());
    expect(
      (await app.request("/v1/contributor/claim", json("token-outsider", { token: "x" }))).status,
    ).toBe(422);
    me.mode = "missing";
    expect(
      (await app.request("/v1/contributor/claim", json("token-outsider", { token: TOKEN }))).status,
    ).toBe(404);
    me.mode = "boom";
    const boom = await app.request(
      "/v1/contributor/claim",
      json("token-outsider", { token: TOKEN }),
    );
    expect(boom.status).toBe(500);
    expect(await boom.text()).not.toContain("PRIVATE");
  });

  it("lists and disconnects the caller's own connections", async () => {
    const { app, me } = setup();
    const list = await app.request(`/v1/workspaces/${WS}/contributor/connections`, {
      headers: bearer("token-outsider"),
    });
    expect(contributorConnectionsResponse.parse(await list.json()).items).toEqual([ROW]);
    const del = await app.request(`/v1/workspaces/${WS}/contributor/connections/${ROW.id}`, {
      method: "DELETE",
      headers: bearer("token-outsider"),
    });
    expect(del.status).toBe(204);
    me.mode = "missing";
    const gone = await app.request(`/v1/workspaces/${WS}/contributor/connections/${ROW.id}`, {
      method: "DELETE",
      headers: bearer("token-outsider"),
    });
    expect(gone.status).toBe(404);
    const bad = await app.request(`/v1/workspaces/${WS}/contributor/connections/nope`, {
      method: "DELETE",
      headers: bearer("token-outsider"),
    });
    expect(bad.status).toBe(422);
  });

  it("starts a provider connection only for an active contributor of that workspace", async () => {
    const { app, store } = setup(true);
    const url = `/v1/workspaces/${WS}/contributor/connections/google/start`;
    const body = { return_to: "/connected" };
    expect((await app.request(url, json("token-owner", body))).status).toBe(404);
    store.contributorUsers.set(`${WS}:${CONTRIBUTOR}`, {
      workspaceId: WS,
      contributorId: randomUUID(),
      email: "ana@example.com",
    });
    const ok = await app.request(url, json("token-owner", body));
    expect(ok.status).toBe(200);
    expect(authorizationUrlResponse.parse(await ok.json()).authorization_url).toContain(
      "accounts.google.com",
    );
    expect(
      (await app.request(url.replace("google", "dropbox"), json("token-owner", body))).status,
    ).toBe(422);
    expect(
      (await app.request(url, json("token-owner", { return_to: "https://evil.example" }))).status,
    ).toBe(422);
  });

  it("answers 503 when the provider is not configured", async () => {
    const { app, store } = setup(false);
    store.contributorUsers.set(`${WS}:${CONTRIBUTOR}`, {
      workspaceId: WS,
      contributorId: randomUUID(),
      email: "ana@example.com",
    });
    const res = await app.request(
      `/v1/workspaces/${WS}/contributor/connections/google/start`,
      json("token-owner", { return_to: "/connected" }),
    );
    expect(res.status).toBe(503);
  });
});

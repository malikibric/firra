import {
  apiError,
  openApiDocument,
  setupReadRoutes,
  setupWriteRoutes,
  type SetupRead,
  type SetupWrite,
} from "@firra/contracts";
import { describe, expect, it } from "vitest";
import type { ReadCaller } from "../src/lib/read-store.js";
import { SetupNotFoundError, type SetupStore } from "../src/lib/setup-store.js";
import { bearer, testApp, USERS } from "./helpers.js";
import { WS, WS_B, ID, ID_B, TOKEN, inputs, reads, writeResult } from "./setup-fixtures.js";

class FakeSetup implements SetupStore {
  calls: {
    caller: ReadCaller;
    workspaceId: string | null;
    operation: string;
    body?: unknown;
    page?: number;
  }[] = [];
  error: Error | null = null;
  write(
    caller: ReadCaller,
    workspaceId: string | null,
    operation: SetupWrite,
    targetId: string | null,
    body: unknown,
  ) {
    this.calls.push({ caller, workspaceId, operation, body });
    if (this.error) return Promise.reject(this.error);
    if (targetId === ID_B) return Promise.reject(new SetupNotFoundError());
    return Promise.resolve(writeResult(operation));
  }
  read(
    caller: ReadCaller,
    workspaceId: string,
    operation: SetupRead,
    targetId?: string,
    page?: number,
  ) {
    this.calls.push({ caller, workspaceId, operation, page });
    if (this.error) return Promise.reject(this.error);
    if (targetId === ID_B) return Promise.reject(new SetupNotFoundError());
    return Promise.resolve(reads[operation]);
  }
}
function setup(limit = 1000) {
  const store = new FakeSetup();
  const t = testApp({
    setupStore: store,
    userRateLimitPerMinute: limit,
    verifyUser: (token) =>
      Promise.resolve(
        token === "token-contributor" ? (USERS["token-outsider"] ?? null) : (USERS[token] ?? null),
      ),
  });
  t.repo.addMember(WS, USERS["token-owner"]?.id ?? "", "owner");
  t.repo.addMember(WS, USERS["token-admin"]?.id ?? "", "admin");
  return { ...t, store };
}
const replaceId = (path: string, id = ID) => path.replace(/:[A-Za-z]+Id/g, id);

for (const route of setupWriteRoutes) {
  const signedOnly = "signedOnly" in route;
  const url = signedOnly ? route.path : `/workspaces/${WS}${route.path}`;
  const send = (
    app: ReturnType<typeof testApp>["app"],
    token = "token-owner",
    body = inputs[route.operation],
    contentType: string | null = "application/json",
    path = url,
  ) =>
    app.request(`/v1${replaceId(path)}`, {
      method: route.method,
      headers: { ...bearer(token), ...(contentType ? { "content-type": contentType } : {}) },
      ...(route.method === "DELETE" ? {} : { body: JSON.stringify(body) }),
    });
  describe(`${route.method} ${url}`, () => {
    it("returns its contract, forwards the caller, and leaves mutation audit to the RPC", async () => {
      const { app, store, repo, logs } = setup();
      const result = await send(
        app,
        "token-owner",
        inputs[route.operation],
        route.method === "DELETE" ? null : "application/json; charset=utf-8",
      );
      expect(result.status).toBe("response" in route ? 200 : 204);
      if ("response" in route) {
        const response: unknown = await result.json();
        expect(route.response.safeParse(response).success).toBe(true);
        if (route.operation === "inviteAdmin" || route.operation === "inviteContributor") {
          expect(response).not.toHaveProperty("link");
          expect(JSON.stringify(response).split(TOKEN)).toHaveLength(2);
        }
      } else expect(await result.text()).toBe("");
      expect(store.calls[0]).toMatchObject({
        caller: { userId: USERS["token-owner"]?.id ?? "", token: "token-owner" },
        workspaceId: signedOnly ? null : WS,
        operation: route.operation,
      });
      expect(repo.audit).toEqual([]);
      expect(logs.join(" ")).not.toContain(TOKEN);
      expect(logs.join(" ")).not.toContain("Synthetic");
      expect(logs.join(" ")).not.toContain("example.com");
    });
    it("requires a valid session", async () => {
      const { app, store } = setup();
      const response = await send(app, "invalid");
      expect(response.status).toBe(401);
      apiError.parse(await response.json());
      expect(store.calls).toEqual([]);
    });
    it("allows the proper role and isolates workspace membership", async () => {
      const { app, store } = setup();
      if (signedOnly) {
        expect((await send(app, "token-outsider")).status).toBe(200);
        return;
      }
      expect((await send(app, "token-admin")).status).toBe("response" in route ? 200 : 204);
      store.calls.length = 0;
      expect((await send(app, "token-outsider")).status).toBe(403);
      expect(
        (
          await send(
            app,
            "token-owner",
            inputs[route.operation],
            "application/json",
            url.replace(WS, WS_B),
          )
        ).status,
      ).toBe(403);
      expect((await send(app, "token-contributor")).status).toBe(403);
      expect(store.calls).toEqual([]);
    });
    it("maps RPC refusal to 404 and errors to a generic envelope without row content", async () => {
      const { app, store, logs } = setup();
      store.error = new SetupNotFoundError();
      const refused = await send(app);
      expect(refused.status).toBe(404);
      apiError.parse(await refused.json());
      store.error = new Error(`secret row ${TOKEN} private@example.com`);
      const failed = await send(app);
      expect(failed.status).toBe(500);
      const response = JSON.stringify(apiError.parse(await failed.json()));
      expect(response + logs.join(" ")).not.toContain(TOKEN);
      expect(response + logs.join(" ")).not.toContain("private@example.com");
    });
    it("enforces per-user limits", async () => {
      const { app } = setup(1);
      await send(app);
      const response = await send(app);
      expect(response.status).toBe(429);
      apiError.parse(await response.json());
    });
    if ("request" in route)
      it("rejects missing JSON content type, malformed JSON and invalid bodies before RPC", async () => {
        const { app, store } = setup();
        expect((await send(app, "token-owner", inputs[route.operation], null)).status).toBe(422);
        expect((await send(app, "token-owner", {})).status).toBe(422);
        const malformed = await app.request(`/v1${replaceId(url)}`, {
          method: route.method,
          headers: { ...bearer("token-owner"), "content-type": "application/json" },
          body: "{",
        });
        expect(malformed.status).toBe(422);
        expect(store.calls).toEqual([]);
      });
    if (!signedOnly)
      it("rejects invalid path ids", async () => {
        const { app, store } = setup();
        expect(
          (
            await send(
              app,
              "token-owner",
              inputs[route.operation],
              "application/json",
              url.replace(WS, "bad"),
            )
          ).status,
        ).toBe(422);
        expect(store.calls).toEqual([]);
      });
  });
}
for (const route of setupReadRoutes) {
  const url = `/v1/workspaces/${WS}${replaceId(route.path)}`;
  describe(`GET ${route.path}`, () => {
    it("returns a validated caller-scoped read and audits counts only", async () => {
      const { app, repo, store, logs } = setup();
      const response = await app.request(url, { headers: bearer("token-owner") });
      expect(response.status).toBe(200);
      const body = route.response.parse(await response.json());
      expect(body).toEqual(reads[route.operation]);
      expect(store.calls[0]?.caller.token).toBe("token-owner");
      expect(repo.audit[0]).toMatchObject({
        workspaceId: WS,
        actorId: USERS["token-owner"]?.id ?? "",
        action: `read.setup_${route.operation}`,
      });
      expect(Object.keys(repo.audit[0]?.metadata ?? {})).toEqual(["count"]);
      expect(response.headers.get("cache-control")).toBe("no-store");
      expect(logs.join(" ") + JSON.stringify(repo.audit)).not.toContain("example.com");
    });
    it("denies outsider, other-workspace and contributor-only access", async () => {
      const { app, store, repo } = setup();
      expect((await app.request(url, { headers: bearer("token-outsider") })).status).toBe(403);
      expect(
        (await app.request(url.replace(WS, WS_B), { headers: bearer("token-owner") })).status,
      ).toBe(403);
      expect((await app.request(url, { headers: bearer("token-contributor") })).status).toBe(403);
      expect(store.calls).toEqual([]);
      expect(repo.audit).toEqual([]);
    });
    it("allows admins, requires authentication and validates workspace ids", async () => {
      const { app } = setup();
      expect((await app.request(url, { headers: bearer("token-admin") })).status).toBe(200);
      expect((await app.request(url)).status).toBe(401);
      expect(
        (await app.request(url.replace(WS, "bad"), { headers: bearer("token-owner") })).status,
      ).toBe(422);
    });
    it("fails closed if auditing fails or the store refuses the read", async () => {
      const { app, repo, store } = setup();
      repo.failAudit = true;
      const response = await app.request(url, { headers: bearer("token-owner") });
      expect(response.status).toBe(500);
      apiError.parse(await response.json());
      repo.failAudit = false;
      store.error = new SetupNotFoundError();
      expect((await app.request(url, { headers: bearer("token-owner") })).status).toBe(404);
      expect(repo.audit).toEqual([]);
    });
    it("enforces a per-user limit", async () => {
      const { app } = setup(1);
      await app.request(url, { headers: bearer("token-owner") });
      expect((await app.request(url, { headers: bearer("token-owner") })).status).toBe(429);
    });
  });
}

describe("setup validation and OpenAPI", () => {
  it("validates exclusions, timezone, replay window, and keyword input", async () => {
    const { app, store } = setup();
    const post = (path: string, body: unknown, method = "POST") =>
      app.request(`/v1${path}`, {
        method,
        headers: { ...bearer("token-owner"), "content-type": "application/json" },
        body: JSON.stringify(body),
      });
    expect(
      (await post(`/workspaces/${WS}/clients`, { name: "Excluded", policy: "excluded" })).status,
    ).toBe(422);
    expect(
      (await post(`/workspaces/${WS}/clients/${ID}/policy`, { policy: "excluded" }, "PUT")).status,
    ).toBe(422);
    expect((await post("/workspaces", { name: "Ws", timezone: "Mars/Crater" })).status).toBe(422);
    expect(
      (
        await post(`/workspaces/${WS}/replay/runs`, {
          window_start: "2026-03-01",
          window_end: "2026-01-01",
        })
      ).status,
    ).toBe(422);
    expect(
      (await post(`/workspaces/${WS}/services`, { name: "Svc", keywords: ["a".repeat(1000), "b"] }))
        .status,
    ).toBe(422);
    expect(store.calls).toEqual([]);
    expect(
      (
        await post(
          `/workspaces/${WS}/clients/${ID}/policy`,
          { policy: "excluded", confirm_exclusion: true },
          "PUT",
        )
      ).status,
    ).toBe(204);
  });
  it("bounds audit paging and forwards valid pages", async () => {
    const { app, store } = setup();
    for (const page of ["0", "201", "-1", "1.5", "abc"])
      expect(
        (
          await app.request(`/v1/workspaces/${WS}/audit?page=${page}`, {
            headers: bearer("token-owner"),
          })
        ).status,
      ).toBe(422);
    expect(
      (await app.request(`/v1/workspaces/${WS}/audit?page=200`, { headers: bearer("token-owner") }))
        .status,
    ).toBe(200);
    expect(store.calls[0]?.page).toBe(200);
  });
  it("shares the per-user budget across setup reads and writes and allows a different user", async () => {
    const { app } = setup(1);
    expect(
      (await app.request(`/v1/workspaces/${WS}/services`, { headers: bearer("token-owner") }))
        .status,
    ).toBe(200);
    const mutation = await app.request(`/v1/workspaces/${WS}/clients`, {
      method: "POST",
      headers: { ...bearer("token-owner"), "content-type": "application/json" },
      body: JSON.stringify(inputs.createClient),
    });
    expect(mutation.status).toBe(429);
    expect(
      (await app.request(`/v1/workspaces/${WS}/members`, { headers: bearer("token-owner") }))
        .status,
    ).toBe(429);
    expect(
      (await app.request(`/v1/workspaces/${WS}/members`, { headers: bearer("token-admin") }))
        .status,
    ).toBe(200);
  });
  it("publishes every setup method and preserves intelligence GET methods on shared paths", () => {
    const doc = openApiDocument("test") as {
      paths: Record<
        string,
        Record<string, { requestBody?: unknown; responses: Record<string, unknown> }>
      >;
    };
    for (const route of setupWriteRoutes) {
      const path = (
        "signedOnly" in route ? route.path : `/workspaces/{workspaceId}${route.path}`
      ).replace(/:([A-Za-z]+Id)/g, "{$1}");
      const operation = doc.paths[path]?.[route.method.toLowerCase()];
      expect(operation).toBeDefined();
      if (route.method === "DELETE") {
        expect(operation?.requestBody).toBeUndefined();
        expect(operation?.responses["204"]).toBeDefined();
      }
    }
    for (const path of [
      "/workspaces/{workspaceId}/clients",
      "/workspaces/{workspaceId}/replay/runs",
    ])
      expect(doc.paths[path]?.get).toBeDefined();
    expect(doc.paths["/workspaces/{workspaceId}/clients"]?.post).toBeDefined();
  });
});

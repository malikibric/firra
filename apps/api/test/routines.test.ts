import {
  apiError,
  mutationResponse,
  openApiDocument,
  routineEnabledRequest,
  routineKind,
  routineLastRun,
  routinesResponse,
} from "@firra/contracts";
import type { RoutineItem, RoutineKind } from "@firra/contracts";
import { afterEach, expect, it, vi } from "vitest";
import { SupabaseReadStore } from "../src/lib/read-store.js";
import { SupabaseWriteStore, WriteForbiddenError } from "../src/lib/write-store.js";
import { required } from "./client-fixtures.js";
import { USERS, bearer, testApp } from "./helpers.js";

const WS = "11111111-1111-4111-8111-111111111111";
const ID = "22222222-2222-4222-8222-222222222222";
const base = `/v1/workspaces/${WS}/routines`;
const run = {
  scheduled_for: "2026-10-04",
  status: "succeeded" as const,
  finished_at: "2026-10-04T08:00:00.000000Z",
  counts: { items: 3, suppressed: 1 },
};
function setup(limit = 1000) {
  const rows = new Map<RoutineKind, boolean>();
  const writes = new SupabaseWriteStore("http://localhost:54321", "public");
  const write = vi
    .spyOn(writes, "setRoutineEnabled")
    .mockImplementation((_caller, _ws, kind, request) => {
      rows.set(kind, request.enabled);
      return Promise.resolve(ID);
    });
  const store = new SupabaseReadStore("http://localhost:54321", "public");
  Object.defineProperty(store, "writes", { value: writes });
  const read = vi.spyOn(store, "routines").mockImplementation(() =>
    Promise.resolve(
      routineKind.options.map((kind): RoutineItem => ({
        kind,
        enabled: rows.get(kind) ?? true,
        created: rows.has(kind),
        last_run: null,
      })),
    ),
  );
  const t = testApp({
    readStore: store,
    userRateLimitPerMinute: limit,
    verifyUser: (token) =>
      Promise.resolve(
        token === "token-contributor"
          ? { id: ID, email: "contributor@example.com" }
          : (USERS[token] ?? null),
      ),
  });
  t.repo.addMember(WS, required(USERS["token-owner"]).id, "owner");
  t.repo.addMember(WS, required(USERS["token-admin"]).id, "admin");
  return { ...t, rows, write, read };
}
const headers = { ...bearer("token-owner"), "content-type": "application/json" };
const put = (enabled: boolean) => ({ method: "PUT", headers, body: JSON.stringify({ enabled }) });
afterEach(() => vi.unstubAllGlobals());

it("contracts reject bad kinds, non-boolean switches, invalid runs and non-numeric counts", () => {
  expect(routineKind.options).toEqual(["morning_scan", "commitment_watch", "pre_meeting_brief"]);
  expect(routineLastRun.parse(run)).toEqual(run);
  for (const body of [{}, { enabled: "true" }, { enabled: 1 }, { enabled: true, params: {} }])
    expect(routineEnabledRequest.safeParse(body).success).toBe(false);
  for (const invalid of [
    { ...run, counts: { items: "PRIVATE" } },
    { ...run, status: "other" },
    { ...run, scheduled_for: "2026-02-30" },
  ])
    expect(routineLastRun.safeParse(invalid).success).toBe(false);
  expect(routinesResponse.safeParse({ generated_at: run.finished_at, items: [] }).success).toBe(
    false,
  );
  const doc = JSON.stringify(openApiDocument("test"));
  for (const text of [
    "/workspaces/{workspaceId}/routines",
    "/workspaces/{workspaceId}/routines/{kind}",
    "getRoutines",
    "setRoutineEnabled",
    "RoutineEnabledRequest",
    "RoutinesResponse",
  ])
    expect(doc).toContain(text);
});

for (const token of ["token-owner", "token-admin"]) {
  it(`allows ${token} to read and switch as caller with lazy defaults and no double audit`, async () => {
    const t = setup();
    const callerHeaders = { ...bearer(token), "content-type": "application/json" };
    const res = await t.app.request(base, { headers: callerHeaders });
    expect(res.status).toBe(200);
    expect(res.headers.get("cache-control")).toBe("no-store");
    expect(routinesResponse.parse(await res.json()).items).toEqual(
      routineKind.options.map((kind) => ({ kind, enabled: true, created: false, last_run: null })),
    );
    expect(t.read).toHaveBeenCalledWith({ token, userId: required(USERS[token]).id }, WS);
    expect(t.repo.audit).toEqual([
      expect.objectContaining({ action: "read.routines", metadata: { count: 3 } }),
    ]);
    const changed = await t.app.request(`${base}/morning_scan`, {
      ...put(false),
      headers: callerHeaders,
    });
    expect(changed.status).toBe(200);
    expect(mutationResponse.parse(await changed.json()).id).toBe(ID);
    expect(changed.headers.get("cache-control")).toBe("no-store");
    expect(t.write).toHaveBeenCalledWith(
      { token, userId: required(USERS[token]).id },
      WS,
      "morning_scan",
      {
        enabled: false,
      },
    );
    expect(t.repo.audit).toHaveLength(1);
  });
}
it("first PUT creates, second changes without duplicating, and GET remains in fixed order", async () => {
  const t = setup();
  for (const enabled of [false, true])
    expect((await t.app.request(`${base}/commitment_watch`, put(enabled))).status).toBe(200);
  expect(t.rows.size).toBe(1);
  const result = routinesResponse.parse(await (await t.app.request(base, { headers })).json());
  expect(result.items[1]).toEqual({
    kind: "commitment_watch",
    enabled: true,
    created: true,
    last_run: null,
  });
});
for (const method of ["GET", "PUT"]) {
  it(`${method} refuses contributor, outsider, foreign workspace and invalid path before storage`, async () => {
    const t = setup();
    const url = method === "GET" ? base : `${base}/morning_scan`;
    for (const [target, token, status] of [
      [url, "token-contributor", 403],
      [url, "token-outsider", 403],
      [url.replace(WS, ID), "token-owner", 403],
      [url.replace(WS, "bad"), "token-owner", 422],
      [url, "invalid", 401],
    ] as const) {
      const response = await t.app.request(target, {
        method,
        headers: { ...bearer(token), "content-type": "application/json" },
        ...(method === "PUT" ? { body: '{"enabled":false}' } : {}),
      });
      expect(response.status).toBe(status);
      apiError.parse(await response.json());
    }
    expect(t.read).not.toHaveBeenCalled();
    expect(t.write).not.toHaveBeenCalled();
    expect(t.repo.audit).toEqual([]);
  });
  it(`${method} applies the per-user limit`, async () => {
    const t = setup(1);
    const url = method === "GET" ? base : `${base}/morning_scan`;
    const request = method === "GET" ? { headers } : put(false);
    await t.app.request(url, request);
    expect((await t.app.request(url, request)).status).toBe(429);
  });
}
it("GET fails closed on audit or read failure; PUT hides forbidden and provider details", async () => {
  const t = setup();
  t.repo.failAudit = true;
  expect((await t.app.request(base, { headers })).status).toBe(500);
  t.read.mockRejectedValue(new Error("PRIVATE ROW"));
  expect((await t.app.request(base, { headers })).status).toBe(500);
  for (const [error, status] of [
    [new WriteForbiddenError(), 404],
    [new Error("PRIVATE ROW"), 500],
  ] as const) {
    t.write.mockRejectedValue(error);
    const response = await t.app.request(`${base}/morning_scan`, put(false));
    expect(response.status).toBe(status);
    expect(await response.text()).not.toContain("PRIVATE");
  }
  expect(t.logs.join()).not.toContain("PRIVATE");
});
it("returns 503 when reads or writes are not configured", async () => {
  const t = testApp();
  t.repo.addMember(WS, required(USERS["token-owner"]).id, "owner");
  for (const [url, request] of [
    [base, { headers }],
    [`${base}/morning_scan`, put(false)],
  ] as const) {
    const response = await t.app.request(url, request);
    expect(response.status).toBe(503);
    expect(apiError.parse(await response.json()).error).toBe("reads_not_configured");
  }
});
it("returns stored numeric counts without logging any row data", async () => {
  const t = setup();
  t.read.mockResolvedValue(
    routineKind.options.map((kind) => ({
      kind,
      enabled: true,
      created: true,
      last_run: { ...run, counts: { PRIVATE_COUNT: 42 } },
    })),
  );
  const response = await t.app.request(base, { headers });
  expect(response.status).toBe(200);
  expect(routinesResponse.parse(await response.json()).items[0]?.last_run?.counts).toEqual({
    PRIVATE_COUNT: 42,
  });
  expect(t.logs.join()).not.toContain("PRIVATE_COUNT");
});
it("PUT rejects unknown kind, malformed JSON, invalid body and missing JSON content type", async () => {
  const t = setup();
  for (const body of ["{", "{}", '{"enabled":"false"}', '{"enabled":null}'])
    expect(
      (await t.app.request(`${base}/morning_scan`, { method: "PUT", headers, body })).status,
    ).toBe(422);
  expect((await t.app.request(`${base}/other`, put(false))).status).toBe(422);
  expect(
    (await t.app.request(`${base}/morning_scan`, { ...put(false), headers: bearer("token-owner") }))
      .status,
  ).toBe(422);
  expect(t.write).not.toHaveBeenCalled();
});
it("production adapters use caller bearer, scoped embedded newest run and sanitized RPC errors", async () => {
  const calls: { url: string; init: RequestInit }[] = [];
  let code: string | null = null;
  vi.stubGlobal("fetch", (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return Promise.resolve(
      new Response(
        JSON.stringify(
          url.includes("/rpc/")
            ? code
              ? { code, message: "PRIVATE ROW" }
              : ID
            : [{ kind: "morning_scan", enabled: false, routine_run: [run] }],
        ),
        { status: code ? 403 : 200, headers: { "content-type": "application/json" } },
      ),
    );
  });
  const caller = { token: "caller-token", userId: ID };
  const reads = new SupabaseReadStore("http://localhost:54321", "public");
  const items = await reads.routines(caller, WS);
  expect(items[0]).toEqual({ kind: "morning_scan", enabled: false, created: true, last_run: run });
  expect(items[1]?.created).toBe(false);
  const url = new URL(required(calls[0]).url);
  expect(url.searchParams.get("workspace_id")).toBe(`eq.${WS}`);
  expect(url.searchParams.get("routine_run.workspace_id")).toBe(`eq.${WS}`);
  expect(url.searchParams.get("routine_run.order")).toBe("scheduled_for.desc");
  expect(url.searchParams.get("routine_run.limit")).toBe("1");
  const writes = new SupabaseWriteStore("http://localhost:54321", "public");
  await writes.setRoutineEnabled(caller, WS, "pre_meeting_brief", { enabled: false });
  expect(JSON.parse(required(calls[1]).init.body as string)).toEqual({
    p_workspace_id: WS,
    p_kind: "pre_meeting_brief",
    p_enabled: false,
  });
  for (const call of calls)
    expect(new Headers(call.init.headers).get("authorization")).toBe("Bearer caller-token");
  code = "42501";
  await expect(
    writes.setRoutineEnabled(caller, WS, "morning_scan", { enabled: true }),
  ).rejects.toBeInstanceOf(WriteForbiddenError);
  code = "XX000";
  await expect(
    writes.setRoutineEnabled(caller, WS, "morning_scan", { enabled: true }),
  ).rejects.toThrow("routine switch failed");
});

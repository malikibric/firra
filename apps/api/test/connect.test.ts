import { createHash, randomUUID } from "node:crypto";
import { describe, expect, it } from "vitest";
import { safeReturnPath, returnUrl } from "../src/connect/pkce.js";
import { monthWindows } from "../src/connect/backfill.js";
import { CALENDAR_SCOPE, GMAIL_SCOPE } from "../src/connect/google.js";
import { CALENDARS_SCOPE, MAIL_SCOPE, normalizeScopes } from "../src/connect/microsoft.js";
import { ConnectService } from "../src/connect/service.js";
import { LocalKek } from "../src/crypto/kek.js";
import { Keyring } from "../src/crypto/keyring.js";
import { createLogger } from "../src/lib/logger.js";
import { TaskService } from "../src/tasks/enqueue.js";
import { bearer, InMemoryRepository, RecordingQueue, testApp, USERS } from "./helpers.js";
import { MemoryConnectStore } from "./memory-connect-store.js";

const CONFIG = {
  clientId: "cid.apps.googleusercontent.com",
  clientSecret: "secret",
  redirectUri: "https://api.example/oauth/google/callback",
};
const sha = (v: string) => createHash("sha256").update(v).digest("hex");

function idToken(claims: Record<string, unknown>): string {
  const part = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64url");
  return `${part({ alg: "RS256" })}.${part(claims)}.sig`;
}

function fakeGoogle(
  overrides: { scope?: string; refresh?: string | null; emailVerified?: boolean } = {},
) {
  const calls: URLSearchParams[] = [];
  const fetchImpl = ((_url: string | URL | Request, init?: RequestInit) => {
    calls.push(new URLSearchParams(init?.body instanceof URLSearchParams ? init.body : undefined));
    return Promise.resolve(
      Response.json({
        ...(overrides.refresh === null ? {} : { refresh_token: overrides.refresh ?? "refresh-1" }),
        id_token: idToken({
          iss: "https://accounts.google.com",
          aud: CONFIG.clientId,
          sub: "google-sub-1",
          email: "Ana@Northbeam.agency",
          email_verified: overrides.emailVerified ?? true,
        }),
        scope: overrides.scope ?? `openid email ${GMAIL_SCOPE}`,
      }),
    );
  }) as typeof fetch;
  return { fetchImpl, calls };
}

function setup(google = fakeGoogle()) {
  const store = new MemoryConnectStore();
  const repo = new InMemoryRepository();
  const queue = new RecordingQueue();
  const keyring = new Keyring(new LocalKek(Buffer.alloc(32, 3)), store);
  const service = new ConnectService({
    store,
    keyring,
    tasks: new TaskService(repo, queue),
    google: CONFIG,
    logger: createLogger(() => undefined),
    fetchImpl: google.fetchImpl,
    now: () => new Date("2026-09-15T12:00:00Z"),
  });
  return { store, repo, queue, keyring, service, google };
}

describe("monthWindows", () => {
  it("covers the last N calendar months, newest first", () => {
    const windows = monthWindows(new Date("2026-09-15T12:00:00Z"), 3);
    expect(windows.map((w) => w.label)).toEqual(["2026-09", "2026-08", "2026-07"]);
    expect(windows[0]?.end.toISOString()).toBe("2026-09-15T12:00:00.000Z");
    expect(windows[1]?.start.toISOString()).toBe("2026-08-01T00:00:00.000Z");
    expect(windows[1]?.end.toISOString()).toBe("2026-09-01T00:00:00.000Z");
    expect(monthWindows(new Date("2026-01-10T00:00:00Z"), 2).map((w) => w.label)).toEqual([
      "2026-01",
      "2025-12",
    ]);
  });
});

describe("ConnectService", () => {
  it("starts with PKCE and stores only a hash of the state", async () => {
    const { service, store } = setup();
    const url = new URL(
      await service.startForMember(randomUUID(), randomUUID(), "/en/w/1/connections"),
    );
    expect(url.origin + url.pathname).toBe("https://accounts.google.com/o/oauth2/v2/auth");
    expect(url.searchParams.get("code_challenge_method")).toBe("S256");
    expect(url.searchParams.get("access_type")).toBe("offline");
    expect(url.searchParams.get("scope")).toContain(GMAIL_SCOPE);
    const state = url.searchParams.get("state") ?? "";
    expect([...store.pending.keys()]).toEqual([sha(state)]);
  });

  it("connects a member mailbox, encrypts the token and starts 12 months of backfill", async () => {
    const { service, store, queue, keyring, google } = setup();
    const workspaceId = randomUUID();
    const userId = randomUUID();
    const state =
      new URL(
        await service.startForMember(workspaceId, userId, "/en/w/1/connections"),
      ).searchParams.get("state") ?? "";

    const result = await service.handleGoogleCallback({ code: "auth-code", state });
    expect(result).toMatchObject({ ok: true, returnTo: "/en/w/1/connections" });
    if (!result.ok) throw new Error("expected success");

    const exchange = google.calls[0];
    expect(exchange?.get("grant_type")).toBe("authorization_code");
    expect(exchange?.get("code_verifier")?.length).toBeGreaterThanOrEqual(43);

    const connection = store.connections.get(result.connectionId);
    expect(connection).toMatchObject({
      provider: "gmail",
      accountLabel: "ana@northbeam.agency",
      externalAccountId: "google-sub-1",
      ownerUserId: userId,
    });
    const token = connection?.token ?? "";
    expect(token).not.toContain("refresh-1");
    expect(JSON.parse(await keyring.decryptToken(workspaceId, result.connectionId, token))).toEqual(
      { refresh_token: "refresh-1" },
    );

    const types = queue.tasks.map((t) => t.envelope.type);
    expect(types.filter((t) => t === "sync.backfill_chunk")).toHaveLength(12);
    expect(types[0]).toBe("sync.incremental");

    // The state is single-use.
    expect(await service.handleGoogleCallback({ code: "auth-code", state })).toMatchObject({
      ok: false,
      error: "invalid_state",
    });
  });

  it("reconnecting re-runs backfill months that failed and leaves finished ones alone", async () => {
    const { service, queue, repo } = setup();
    const workspaceId = randomUUID();
    const userId = randomUUID();
    const connect = async () => {
      const url = await service.startForMember(workspaceId, userId, "/en/x");
      await service.handleGoogleCallback({
        code: "c",
        state: new URL(url).searchParams.get("state") ?? "",
      });
    };
    await connect();
    const months = repo.jobs.filter((j) => j.type === "sync.backfill_chunk");
    Object.assign(months[0] ?? {}, { status: "failed", attempts: 5 });
    months.slice(1).forEach((j) => (j.status = "succeeded"));
    queue.tasks.length = 0;

    await connect();
    const backfills = queue.tasks.filter((t) => t.envelope.type === "sync.backfill_chunk");
    expect(backfills.map((t) => t.envelope.job_id)).toEqual([months[0]?.id]);
    expect(months[0]).toMatchObject({ status: "queued", attempts: 0 });
  });

  it("also connects Calendar when it was granted, each with its own ciphertext", async () => {
    const { service, store, queue } = setup(
      fakeGoogle({ scope: `openid email ${GMAIL_SCOPE} ${CALENDAR_SCOPE}` }),
    );
    const url = await service.startForMember(randomUUID(), randomUUID(), "/en/x");
    await service.handleGoogleCallback({
      code: "c",
      state: new URL(url).searchParams.get("state") ?? "",
    });
    const connections = [...store.connections.values()];
    expect(connections.map((c) => c.provider).sort()).toEqual(["gmail", "google_calendar"]);
    expect(connections[0]?.token).not.toBe(connections[1]?.token);
    expect(queue.tasks.filter((t) => t.envelope.type === "sync.backfill_chunk")).toHaveLength(24);
  });

  it.each([
    ["missing Gmail scope", { scope: "openid email" }, "missing_scope"],
    ["no refresh token", { refresh: null }, "no_refresh_token"],
    ["unverified email", { emailVerified: false }, "unverified_email"],
  ] as const)("rejects %s without storing anything", async (_name, overrides, error) => {
    const { service, store, queue } = setup(fakeGoogle(overrides));
    const state =
      new URL(await service.startForMember(randomUUID(), randomUUID(), "/en/x")).searchParams.get(
        "state",
      ) ?? "";
    expect(await service.handleGoogleCallback({ code: "c", state })).toMatchObject({
      ok: false,
      error,
      returnTo: "/en/x",
    });
    expect(store.connections.size).toBe(0);
    expect(queue.tasks).toHaveLength(0);
  });

  it("reports a denied consent back to the page that started it", async () => {
    const { service } = setup();
    const state =
      new URL(await service.startForMember(randomUUID(), randomUUID(), "/en/x")).searchParams.get(
        "state",
      ) ?? "";
    expect(await service.handleGoogleCallback({ error: "access_denied", state })).toEqual({
      ok: false,
      returnTo: "/en/x",
      error: "access_denied",
    });
  });
});

describe("connect routes", () => {
  it("requires an owner or admin and a same-app return path; 503 when Google is not configured", async () => {
    const { app, repo } = testApp();
    const workspaceId = randomUUID();
    const owner = USERS["token-owner"];
    if (!owner) throw new Error("fixture");
    repo.addMember(workspaceId, owner.id, "owner");
    const start = (token: string | null, body: unknown) =>
      app.request(`/v1/workspaces/${workspaceId}/connections/google/start`, {
        method: "POST",
        headers: token ? bearer(token) : {},
        body: JSON.stringify(body),
      });
    expect((await start(null, { return_to: "/en/x" })).status).toBe(401);
    expect((await start("token-outsider", { return_to: "/en/x" })).status).toBe(403);
    expect((await start("token-owner", { return_to: "https://evil.example" })).status).toBe(400);
    expect((await start("token-owner", { return_to: "//evil.example" })).status).toBe(400);
    expect((await start("token-owner", { return_to: "/en/x" })).status).toBe(503);
    const other = await app.request(`/v1/workspaces/${workspaceId}/connections/yahoo/start`, {
      method: "POST",
      headers: bearer("token-owner"),
      body: JSON.stringify({ return_to: "/en/x" }),
    });
    expect(other.status).toBe(400);
    const microsoft = await app.request(
      `/v1/workspaces/${workspaceId}/connections/microsoft/start`,
      {
        method: "POST",
        headers: bearer("token-owner"),
        body: JSON.stringify({ return_to: "/en/x" }),
      },
    );
    expect(microsoft.status).toBe(503);
    expect(await microsoft.json()).toEqual({ error: "microsoft_not_configured" });
  });

  it("redirects callbacks with an unknown state back to the web app with an error", async () => {
    const { app } = testApp();
    const response = await app.request("/oauth/google/callback?code=x&state=unknown");
    expect(response.status).toBe(302);
    expect(response.headers.get("location")).toBe(
      "https://app.example/?connect_error=invalid_state",
    );
    const microsoft = await app.request("/oauth/microsoft/callback?code=x&state=unknown");
    expect(microsoft.headers.get("location")).toBe(
      "https://app.example/?connect_error=invalid_state",
    );
  });
});

const MS_CONFIG = {
  clientId: "11111111-2222-3333-4444-555555555555",
  clientSecret: "secret",
  redirectUri: "https://api.example/oauth/microsoft/callback",
  tenant: "organizations",
};
const TENANT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";

function fakeMicrosoft(
  overrides: {
    scope?: string;
    refresh?: string | null;
    mail?: string | null;
    iss?: string;
    meId?: string;
  } = {},
) {
  const urls: string[] = [];
  const forms: URLSearchParams[] = [];
  const fetchImpl = ((input: string | URL | Request, init?: RequestInit) => {
    const url = input instanceof Request ? input.url : input.toString();
    urls.push(url);
    if (url.startsWith("https://graph.microsoft.com/")) {
      return Promise.resolve(
        Response.json({
          id: overrides.meId ?? "oid-1",
          mail: overrides.mail === undefined ? "Ana@Northbeam.agency" : overrides.mail,
          userPrincipalName: null,
        }),
      );
    }
    forms.push(new URLSearchParams(init?.body instanceof URLSearchParams ? init.body : undefined));
    return Promise.resolve(
      Response.json({
        access_token: "graph-access",
        ...(overrides.refresh === null ? {} : { refresh_token: overrides.refresh ?? "ms-refresh" }),
        id_token: idToken({
          iss: overrides.iss ?? `https://login.microsoftonline.com/${TENANT}/v2.0`,
          aud: MS_CONFIG.clientId,
          tid: TENANT,
          oid: "oid-1",
        }),
        scope:
          overrides.scope ??
          "openid email profile offline_access https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Calendars.Read",
      }),
    );
  }) as typeof fetch;
  return { fetchImpl, urls, forms };
}

function setupMicrosoft(microsoft = fakeMicrosoft()) {
  const store = new MemoryConnectStore();
  const queue = new RecordingQueue();
  const keyring = new Keyring(new LocalKek(Buffer.alloc(32, 4)), store);
  const service = new ConnectService({
    store,
    keyring,
    tasks: new TaskService(new InMemoryRepository(), queue),
    google: CONFIG,
    microsoft: MS_CONFIG,
    logger: createLogger(() => undefined),
    fetchImpl: microsoft.fetchImpl,
    now: () => new Date("2026-09-15T12:00:00Z"),
  });
  return { store, queue, keyring, service, microsoft };
}

const stateOf = (url: string) => new URL(url).searchParams.get("state") ?? "";

describe("ConnectService (Microsoft)", () => {
  it("starts at the configured tenant with PKCE and offline access", async () => {
    const { service } = setupMicrosoft();
    const url = new URL(
      await service.startForMember(randomUUID(), randomUUID(), "/en/x", "microsoft"),
    );
    expect(url.origin + url.pathname).toBe(
      "https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize",
    );
    expect(url.searchParams.get("code_challenge_method")).toBe("S256");
    expect(url.searchParams.get("response_mode")).toBe("query");
    expect(url.searchParams.get("scope")?.split(" ")).toEqual(
      expect.arrayContaining(["offline_access", MAIL_SCOPE, CALENDARS_SCOPE]),
    );
  });

  it("connects Outlook mail and calendar with the tenant stored next to the refresh token", async () => {
    const { service, store, queue, keyring, microsoft } = setupMicrosoft();
    const workspaceId = randomUUID();
    const url = await service.startForMember(workspaceId, randomUUID(), "/en/x", "microsoft");
    const result = await service.handleMicrosoftCallback({ code: "c", state: stateOf(url) });
    if (!result.ok) throw new Error(`expected success, got ${result.error}`);

    expect(microsoft.forms[0]?.get("code_verifier")?.length).toBeGreaterThanOrEqual(43);
    const connections = [...store.connections.values()];
    expect(connections.map((c) => c.provider).sort()).toEqual(["outlook", "outlook_calendar"]);
    expect(connections[0]).toMatchObject({
      accountLabel: "ana@northbeam.agency",
      externalAccountId: "oid-1",
    });
    const stored = await keyring.decryptToken(
      workspaceId,
      result.connectionId,
      connections[0]?.token ?? "",
    );
    expect(JSON.parse(stored)).toEqual({ refresh_token: "ms-refresh", tenant_id: TENANT });
    expect(queue.tasks.filter((t) => t.envelope.type === "sync.backfill_chunk")).toHaveLength(24);
  });

  it("connects mail only when calendar access was not granted (short scope names)", async () => {
    const { service, store } = setupMicrosoft(
      fakeMicrosoft({ scope: "openid profile email offline_access Mail.Read" }),
    );
    const url = await service.startForMember(randomUUID(), randomUUID(), "/en/x", "microsoft");
    expect(await service.handleMicrosoftCallback({ code: "c", state: stateOf(url) })).toMatchObject(
      {
        ok: true,
      },
    );
    expect([...store.connections.values()].map((c) => c.provider)).toEqual(["outlook"]);
  });

  it.each([
    ["missing mail scope", { scope: "openid offline_access" }, "missing_scope"],
    ["no refresh token", { refresh: null }, "no_refresh_token"],
    ["no mailbox address", { mail: null }, "no_mailbox"],
    ["foreign issuer", { iss: "https://evil.example/v2.0" }, "exchange_failed"],
    ["profile of another user", { meId: "oid-2" }, "exchange_failed"],
  ] as const)("rejects %s without storing anything", async (_name, overrides, error) => {
    const { service, store, queue } = setupMicrosoft(fakeMicrosoft(overrides));
    const url = await service.startForMember(randomUUID(), randomUUID(), "/en/x", "microsoft");
    expect(await service.handleMicrosoftCallback({ code: "c", state: stateOf(url) })).toMatchObject(
      {
        ok: false,
        error,
      },
    );
    expect(store.connections.size).toBe(0);
    expect(queue.tasks).toHaveLength(0);
  });

  it("only accepts a state at the callback of the provider it was issued for", async () => {
    const { service, store } = setupMicrosoft();
    const url = await service.startForMember(randomUUID(), randomUUID(), "/en/x", "google");
    expect(await service.handleMicrosoftCallback({ code: "c", state: stateOf(url) })).toEqual({
      ok: false,
      returnTo: null,
      error: "invalid_state",
    });
    expect(store.connections.size).toBe(0);
  });

  it("refuses to start when Microsoft is not configured", async () => {
    const { service, store } = setup();
    await expect(
      service.startForMember(randomUUID(), randomUUID(), "/en/x", "microsoft"),
    ).rejects.toThrow("microsoft oauth is not configured");
    expect(store.pending.size).toBe(0);
  });
});

describe("normalizeScopes", () => {
  it("maps short and lower-case Graph scopes to full URIs", () => {
    expect(normalizeScopes("mail.read https://graph.microsoft.com/calendars.read openid")).toEqual([
      MAIL_SCOPE,
      CALENDARS_SCOPE,
      "openid",
    ]);
  });
});

describe("native OAuth return paths", () => {
  const hostile = [
    "firra://evil.com",
    "firra:///..",
    "https://evil.example",
    "javascript:alert(1)",
    "firra://connected//evil",
    "firra://connected?next=https://evil.example",
    "firra://user@evil",
    "Firra://connected",
    "firra://connected%2f%2fevil",
    "//evil.example",
    "/\\evil.example",
    "/\nevil.example",
  ];
  it.each(hostile)("rejects hostile return path %s", (value) => {
    expect(safeReturnPath(value)).toBeNull();
    expect(returnUrl(value, "https://app.example")?.origin).toBe("https://app.example");
  });
  it.each([
    "firra://connected",
    "firra://auth/callback",
    "firra:///connected",
    "firra://w/client_1-2",
    "/en/w/123/connections?tab=mail",
  ])("accepts app destination %s", (value) => {
    expect(safeReturnPath(value)).toBe(value);
  });
  for (const provider of ["google", "microsoft"] as const) {
    it(`${provider}: starts through the member route and redirects success to the native app`, async () => {
      const { service } = provider === "google" ? setup() : setupMicrosoft();
      const { app, repo, logs } = testApp({ connect: service });
      const ws = randomUUID();
      repo.addMember(ws, USERS["token-owner"]?.id ?? "", "owner");
      const started = await app.request(`/v1/workspaces/${ws}/connections/${provider}/start`, {
        method: "POST",
        headers: bearer("token-owner"),
        body: JSON.stringify({ return_to: "firra://connected" }),
      });
      expect(started.status).toBe(200);
      const { authorization_url: url } = (await started.json()) as { authorization_url: string };
      const response = await app.request(
        `/oauth/${provider}/callback?code=c&state=${stateOf(url)}`,
      );
      expect(response.status).toBe(302);
      expect(response.headers.get("location")).toBe(`firra://connected?connected=${provider}`);
      expect(logs.join(" ")).not.toContain("refresh-1");
      expect(logs.join(" ")).not.toContain("ms-refresh");
    });
    it(`${provider}: redirects consent denial to native app and preserves relative web destinations`, async () => {
      const { service } = provider === "google" ? setup() : setupMicrosoft();
      const { app } = testApp({ connect: service });
      for (const destination of ["firra://auth/callback", "/en/w/1/connections?tab=mail"]) {
        const url = await service.startForMember(randomUUID(), randomUUID(), destination, provider);
        const response = await app.request(
          `/oauth/${provider}/callback?error=access_denied&state=${stateOf(url)}`,
        );
        const target = new URL(response.headers.get("location") ?? "");
        expect(target.searchParams.get("connect_error")).toBe("access_denied");
        expect(target.protocol).toBe(destination.startsWith("firra:") ? "firra:" : "https:");
        if (!destination.startsWith("firra:")) {
          expect(target.origin).toBe("https://app.example");
          expect(target.searchParams.get("tab")).toBe("mail");
        }
      }
    });
    it.each(hostile)(
      `${provider}: rejects hostile input and revalidates a corrupt stored destination %s`,
      async (destination) => {
        const { service, store } = provider === "google" ? setup() : setupMicrosoft();
        const { app, repo } = testApp({ connect: service });
        const ws = randomUUID();
        repo.addMember(ws, USERS["token-owner"]?.id ?? "", "owner");
        const start = await app.request(`/v1/workspaces/${ws}/connections/${provider}/start`, {
          method: "POST",
          headers: bearer("token-owner"),
          body: JSON.stringify({ return_to: destination }),
        });
        expect(start.status).toBe(400);
        expect(store.pending.size).toBe(0);
        const url = await service.startForMember(
          ws,
          USERS["token-owner"]?.id ?? "",
          destination,
          provider,
        );
        const response = await app.request(
          `/oauth/${provider}/callback?error=access_denied&state=${stateOf(url)}`,
        );
        expect(response.headers.get("location")).toBe(
          "https://app.example/?connect_error=access_denied",
        );
      },
    );
  }
});

describe("callbacks without the web app", () => {
  for (const provider of ["google", "microsoft"] as const) {
    it(`${provider}: redirects native destinations and renders a static page for legacy paths`, async () => {
      const { service } = provider === "google" ? setup() : setupMicrosoft();
      const { app } = testApp({ connect: service, webUrl: undefined });
      for (const destination of ["firra://connected", "/en/w/1/connections"]) {
        const url = await service.startForMember(randomUUID(), randomUUID(), destination, provider);
        const response = await app.request(
          `/oauth/${provider}/callback?code=c&state=${stateOf(url)}`,
        );
        if (destination.startsWith("firra:")) {
          expect(response.status).toBe(302);
          expect(response.headers.get("location")).toBe(`firra://connected?connected=${provider}`);
        } else {
          expect(response.status).toBe(200);
          expect(response.headers.get("location")).toBeNull();
          expect(response.headers.get("cache-control")).toBe("no-store");
          expect(response.headers.get("content-type")).toContain("text/html");
          const page = await response.text();
          expect(page).toContain("The connection finished.");
          expect(page).toContain("return to the Firra app");
          expect(page).not.toMatch(/<script|https?:|code=c/);
        }
      }
      const failed = await app.request(`/oauth/${provider}/callback?code=c&state=unknown`);
      expect(failed.status).toBe(200);
      expect(await failed.text()).toContain("could not be completed");
    });
  }
  it.each(["google/start", "microsoft/start", "status", "disconnect"])(
    "removed contributor token route %s returns 404",
    async (path) => {
      const { app } = testApp();
      const response = await app.request(`/connect/contributor/${path}`, {
        method: "POST",
        body: JSON.stringify({ token: "a".repeat(64), return_to: "firra://connected" }),
      });
      expect(response.status).toBe(404);
    },
  );
  it("does not infer a web origin for corrupt or relative destinations", () => {
    for (const destination of [null, "/en/x", "https://evil.example", "//evil.example"])
      expect(returnUrl(destination, undefined)).toBeNull();
    expect(returnUrl("firra://connected", undefined)?.protocol).toBe("firra:");
  });
  it("finishes a signed-in contributor OAuth flow with contributor ownership intact", async () => {
    const { service, store } = setup();
    const { app, logs } = testApp({ connect: service, webUrl: undefined });
    const ws = randomUUID(),
      contributorId = randomUUID();
    store.contributorUsers.set(`${ws}:${USERS["token-outsider"]?.id ?? ""}`, {
      workspaceId: ws,
      contributorId,
      email: "dev@northbeam.agency",
    });
    const start = await app.request(`/v1/workspaces/${ws}/contributor/connections/google/start`, {
      method: "POST",
      headers: bearer("token-outsider"),
      body: JSON.stringify({ return_to: "firra://connected" }),
    });
    expect(start.status).toBe(200);
    const { authorization_url: url } = (await start.json()) as { authorization_url: string };
    const callback = await app.request(`/oauth/google/callback?code=c&state=${stateOf(url)}`);
    expect(callback.headers.get("location")).toBe("firra://connected?connected=google");
    expect([...store.connections.values()][0]).toMatchObject({
      ownerContributorId: contributorId,
      ownerUserId: null,
    });
    expect(store.activated).toEqual([contributorId]);
    expect(logs.join(" ")).not.toContain("refresh-1");
  });
});

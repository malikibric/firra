import { randomUUID } from "node:crypto";
import { describe, expect, it } from "vitest";
import { OrgService } from "../src/connect/org.js";
import { LocalKek } from "../src/crypto/kek.js";
import { Keyring } from "../src/crypto/keyring.js";
import { createLogger } from "../src/lib/logger.js";
import { TaskService } from "../src/tasks/enqueue.js";
import { bearer, InMemoryRepository, RecordingQueue, testApp, USERS } from "./helpers.js";
import { MemoryConnectStore } from "./memory-connect-store.js";

const TENANT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";
const OTHER_TENANT = "ffffffff-bbbb-cccc-dddd-eeeeeeeeeeee";
const MS = {
  clientId: "11111111-2222-3333-4444-555555555555",
  clientSecret: "secret",
  redirectUri: "https://api.example/oauth/microsoft/callback",
  tenant: "organizations",
};

function fakeGraph(options: { consent?: boolean; mailboxes?: string[] } = {}) {
  const tokenTenants: string[] = [];
  const fetchImpl = ((input: string | URL | Request) => {
    const url = input instanceof Request ? input.url : input.toString();
    if (url.startsWith("https://login.microsoftonline.com/")) {
      tokenTenants.push(url.split("/")[3] ?? "");
      return Promise.resolve(
        options.consent === false
          ? Response.json({ error: "invalid_client" }, { status: 400 })
          : Response.json({ access_token: "app-token" }),
      );
    }
    const email = decodeURIComponent(url.split("/users/")[1]?.split("/")[0] ?? "");
    return Promise.resolve(
      (options.mailboxes ?? []).includes(email)
        ? Response.json({ id: "inbox" })
        : Response.json({ error: { code: "ErrorInvalidUser" } }, { status: 404 }),
    );
  }) as typeof fetch;
  return { fetchImpl, tokenTenants };
}

async function setup(graph = fakeGraph(), withOwnOutlook = true) {
  const store = new MemoryConnectStore();
  const keyring = new Keyring(new LocalKek(Buffer.alloc(32, 5)), store);
  const queue = new RecordingQueue();
  const workspaceId = randomUUID();
  const userId = randomUUID();
  if (withOwnOutlook) {
    const id = await store.upsertConnection({
      workspaceId,
      provider: "outlook",
      ownerUserId: userId,
      ownerContributorId: null,
      accountLabel: "ana@northbeam.agency",
      externalAccountId: "oid-owner",
      scopes: [],
    });
    const blob = await keyring.encryptToken(
      workspaceId,
      id,
      JSON.stringify({ refresh_token: "r", tenant_id: TENANT }),
    );
    await store.saveToken(workspaceId, id, blob);
  }
  const org = new OrgService({
    store,
    keyring,
    tasks: new TaskService(new InMemoryRepository(), queue),
    logger: createLogger(() => undefined),
    googleDelegationClientId: "123456789012345678901",
    microsoft: MS,
    microsoftAdminRedirectUri: "https://api.example/oauth/microsoft/admin-callback",
    fetchImpl: graph.fetchImpl,
    backfillMonths: 1,
  });
  return { org, store, queue, keyring, workspaceId, userId, graph };
}

const stateOf = (url: string) => new URL(url).searchParams.get("state") ?? "";

describe("OrgService (Microsoft application permissions)", () => {
  it("asks for admin consent only in the member's verified tenant", async () => {
    const { org, workspaceId, userId } = await setup();
    const url = new URL((await org.microsoftConsentUrl(workspaceId, userId, "/en/x")) ?? "");
    expect(url.pathname).toBe(`/${TENANT}/v2.0/adminconsent`);
    expect(url.searchParams.get("scope")).toBe("https://graph.microsoft.com/.default");
    expect(url.searchParams.get("redirect_uri")).toBe(
      "https://api.example/oauth/microsoft/admin-callback",
    );
    expect(await org.status(workspaceId, userId)).toMatchObject({
      microsoft: { tenant_id: TENANT },
    });
  });

  it("refuses a consent callback that names another tenant", async () => {
    const { org, store, workspaceId, userId } = await setup();
    const url = (await org.microsoftConsentUrl(workspaceId, userId, "/en/x")) ?? "";
    expect(
      await org.handleAdminConsent({
        state: stateOf(url),
        tenant: OTHER_TENANT,
        adminConsent: "True",
      }),
    ).toEqual({ ok: false, returnTo: "/en/x", error: "tenant_mismatch" });
    const again = (await org.microsoftConsentUrl(workspaceId, userId, "/en/x")) ?? "";
    expect(
      await org.handleAdminConsent({ state: stateOf(again), tenant: TENANT, adminConsent: "True" }),
    ).toEqual({ ok: true, returnTo: "/en/x" });
    expect(store.audits.map((a) => a.action)).toEqual(["connection.org_consent_granted"]);
  });

  it("does not accept ordinary sign-in states at the admin callback", async () => {
    const { org } = await setup();
    expect(
      await org.handleAdminConsent({ state: "made-up", tenant: TENANT, adminConsent: "True" }),
    ).toMatchObject({ ok: false, error: "invalid_state" });
  });

  it("adds reachable mailboxes of that tenant with the tenant stored for the worker", async () => {
    const graph = fakeGraph({ mailboxes: ["ben@northbeam.agency"] });
    const { org, store, keyring, queue, workspaceId, userId } = await setup(graph);
    const results = await org.addMicrosoftMailboxes(workspaceId, userId, [
      "ben@northbeam.agency",
      "ghost@northbeam.agency",
    ]);
    expect(results).toEqual([
      { email: "ben@northbeam.agency", status: "added" },
      { email: "ghost@northbeam.agency", status: "not_found" },
    ]);
    expect(graph.tokenTenants).toEqual([TENANT]);
    const added = [...store.connections.values()].filter((c) => c.mode === "org_app");
    expect(added.map((c) => c.provider).sort()).toEqual(["outlook", "outlook_calendar"]);
    expect(added[0]).toMatchObject({
      externalAccountId: "ben@northbeam.agency",
      ownerUserId: null,
    });
    const stored = await keyring.decryptToken(
      workspaceId,
      added[0]?.id ?? "",
      added[0]?.token ?? "",
    );
    expect(JSON.parse(stored)).toEqual({ tenant_id: TENANT });
    expect(store.audits.filter((a) => a.action === "connection.org_added")).toHaveLength(2);
    expect(queue.tasks.filter((t) => t.envelope.type === "sync.incremental")).toHaveLength(2);
  });

  it("reports missing consent and needs the member's own Outlook connection", async () => {
    const noConsent = await setup(fakeGraph({ consent: false }));
    expect(
      await noConsent.org.addMicrosoftMailboxes(noConsent.workspaceId, noConsent.userId, [
        "a@b.co",
      ]),
    ).toEqual([{ email: "a@b.co", status: "consent_missing" }]);
    const noTenant = await setup(fakeGraph(), false);
    expect(
      await noTenant.org.microsoftConsentUrl(noTenant.workspaceId, noTenant.userId, "/x"),
    ).toBeNull();
    expect(
      await noTenant.org.addMicrosoftMailboxes(noTenant.workspaceId, noTenant.userId, ["a@b.co"]),
    ).toBeNull();
  });
});

describe("OrgService (Google domain-wide delegation)", () => {
  it("adds delegated Gmail and Calendar connections without tokens", async () => {
    const { org, store, workspaceId, userId } = await setup();
    expect(await org.addGoogleMailboxes(workspaceId, userId, ["ben@northbeam.agency"])).toEqual([
      { email: "ben@northbeam.agency", status: "added" },
    ]);
    const added = [...store.connections.values()].filter((c) => c.mode === "org_delegated");
    expect(added.map((c) => c.provider).sort()).toEqual(["gmail", "google_calendar"]);
    expect(added.every((c) => c.token === undefined)).toBe(true);
    expect(await org.status(workspaceId, userId)).toMatchObject({
      google: { client_id: "123456789012345678901" },
    });
  });
});

describe("org routes", () => {
  it("are for owners and admins and validate mailbox lists", async () => {
    const { app, repo } = testApp();
    const workspaceId = randomUUID();
    const owner = USERS["token-owner"];
    if (!owner) throw new Error("fixture");
    repo.addMember(workspaceId, owner.id, "owner");
    const post = (path: string, body: unknown, token = "token-owner") =>
      app.request(`/v1/workspaces/${workspaceId}/org${path}`, {
        method: "POST",
        headers: bearer(token),
        body: JSON.stringify(body),
      });
    expect((await post("/status", {}, "token-outsider")).status).toBe(403);
    expect(await (await post("/status", {})).json()).toEqual({ google: null, microsoft: null });
    expect((await post("/google/mailboxes", { emails: ["not-an-email"] })).status).toBe(400);
    expect((await post("/google/mailboxes", { emails: ["a@b.co"] })).status).toBe(503);
    expect((await post("/microsoft/consent", { return_to: "//evil" })).status).toBe(400);
    const callback = await app.request("/oauth/microsoft/admin-callback?state=x&tenant=y");
    expect(callback.headers.get("location")).toBe("https://app.example/?org_error=invalid_state");
  });
});

it("admin consent without WEB_URL redirects native paths and renders static legacy completion", async () => {
  const { org, workspaceId, userId } = await setup();
  const { app } = testApp({ org, webUrl: undefined });
  for (const destination of ["firra://connected", "/en/x"]) {
    const url = (await org.microsoftConsentUrl(workspaceId, userId, destination)) ?? "";
    const response = await app.request(
      `/oauth/microsoft/admin-callback?state=${stateOf(url)}&tenant=${TENANT}&admin_consent=True`,
    );
    if (destination.startsWith("firra:")) {
      expect(response.status).toBe(302);
      expect(response.headers.get("location")).toBe("firra://connected?org_consent=microsoft");
    } else {
      expect(response.status).toBe(200);
      expect(response.headers.get("cache-control")).toBe("no-store");
      expect(response.headers.get("location")).toBeNull();
      const page = await response.text();
      expect(page).toContain("The connection finished.");
      expect(page).not.toMatch(/<script|https?:|tenant=/);
    }
  }
  const failed = await app.request("/oauth/microsoft/admin-callback?state=unknown");
  expect(failed.status).toBe(200);
  expect(await failed.text()).toContain("could not be completed");
});

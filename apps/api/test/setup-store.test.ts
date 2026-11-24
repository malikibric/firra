import { afterEach, describe, expect, it, vi } from "vitest";
import type { SetupWrite } from "@firra/contracts";
import { SetupNotFoundError, SupabaseSetupStore } from "../src/lib/setup-store.js";
import { inviteEmailSender } from "../src/lib/invite-email.js";
import { AT, ID, ID_B, WS, TOKEN, inputs } from "./setup-fixtures.js";

const caller = { userId: ID, token: "caller-session" };
const key = "publishable-key-not-a-service-secret";
const rpcNames: Record<SetupWrite, string> = {
  createWorkspace: "create_workspace",
  acceptInvite: "accept_workspace_invite",
  createClient: "create_client",
  updateClient: "update_client",
  setDomains: "set_client_domains",
  setPolicy: "set_client_policy",
  createService: "save_workspace_service",
  saveService: "save_workspace_service",
  deleteService: "delete_workspace_service",
  inviteAdmin: "create_workspace_invite",
  revokeInvite: "revoke_workspace_invite",
  removeMember: "remove_member",
  inviteContributor: "create_contributor_invite",
  revokeContributor: "revoke_contributor",
  disconnect: "disconnect_connection",
  requestReplay: "request_replay_run",
};
function fakeDb(options: { code?: string; missing?: boolean } = {}) {
  const calls: { url: URL; init: RequestInit }[] = [];
  const send = vi.fn((input: string | URL | Request, init: RequestInit = {}) => {
    const url = new URL(input instanceof Request ? input.url : input.toString());
    calls.push({ url, init });
    if (url.pathname.includes("/rpc/")) {
      if (options.code)
        return Promise.resolve(
          Response.json({ code: options.code, message: `secret ${TOKEN}` }, { status: 400 }),
        );
      if (url.pathname.endsWith("create_workspace_invite"))
        return Promise.resolve(Response.json([{ invite_id: ID, token: TOKEN, expires_at: AT }]));
      if (url.pathname.endsWith("create_contributor_invite"))
        return Promise.resolve(Response.json([{ contributor_id: ID, token: TOKEN }]));
      if (
        [
          "create_workspace",
          "accept_workspace_invite",
          "create_client",
          "save_workspace_service",
          "request_replay_run",
        ].some((name) => url.pathname.endsWith(name))
      )
        return Promise.resolve(Response.json(ID));
      return Promise.resolve(new Response(null, { status: 204 }));
    }
    if (url.pathname.endsWith("/workspace"))
      return Promise.resolve(Response.json({ name: "Synthetic workspace" }));
    return Promise.resolve(
      Response.json(
        options.missing || url.searchParams.get("id") === `eq.${ID_B}` ? [] : [{ id: ID }],
      ),
    );
  });
  vi.stubGlobal("fetch", send);
  const store = new SupabaseSetupStore("http://localhost:54321", key);
  return { store, calls };
}
afterEach(() => vi.unstubAllGlobals());

describe("setup RPCs as caller", () => {
  for (const [operation, rpc] of Object.entries(rpcNames) as [SetupWrite, string][]) {
    it(`${operation} calls ${rpc} using the caller bearer and public key`, async () => {
      const { store, calls } = fakeDb();
      const response = await store.write(caller, WS, operation, ID, inputs[operation]);
      const invoked = calls.filter((call) => call.url.pathname.includes("/rpc/"));
      expect(invoked).toHaveLength(1);
      expect(invoked[0]?.url.pathname).toBe(`/rest/v1/rpc/${rpc}`);
      for (const call of calls) {
        const headers = new Headers(call.init.headers);
        expect(headers.get("authorization")).toBe("Bearer caller-session");
        expect(headers.get("apikey")).toBe(key);
      }
      const args = JSON.parse(invoked[0]?.init.body as string) as Record<string, unknown>;
      if (operation === "createService") expect(args.p_service_id).toBeNull();
      if (operation === "saveService") expect(args.p_service_id).toBe(ID);
      if (operation === "disconnect") expect(args).toEqual({ p_connection_id: ID });
      if (operation === "inviteAdmin")
        expect(response).toMatchObject({ token: TOKEN, emailed: false });
    });
    it(`${operation} maps 42501 only to a not-found refusal and hides other RPC messages`, async () => {
      let fixture = fakeDb({ code: "42501" });
      await expect(
        fixture.store.write(caller, WS, operation, ID, inputs[operation]),
      ).rejects.toBeInstanceOf(SetupNotFoundError);
      fixture = fakeDb({ code: "22023" });
      await expect(
        fixture.store.write(caller, WS, operation, ID, inputs[operation]),
      ).rejects.toThrow("setup operation failed");
    });
  }
  it.each([
    "updateClient",
    "setDomains",
    "setPolicy",
    "saveService",
    "deleteService",
    "revokeInvite",
    "revokeContributor",
    "disconnect",
  ] as const)("%s cannot target another workspace through the URL", async (operation) => {
    const { store, calls } = fakeDb({ missing: true });
    await expect(
      store.write(caller, WS, operation, ID_B, inputs[operation]),
    ).rejects.toBeInstanceOf(SetupNotFoundError);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url.searchParams.get("workspace_id")).toBe(`eq.${WS}`);
    expect(calls[0]?.url.searchParams.get("id")).toBe(`eq.${ID_B}`);
    expect(calls.some((call) => call.url.pathname.includes("/rpc/"))).toBe(false);
  });
  it("normalizes service keywords and invite email addresses without exposing them in errors", async () => {
    const { store, calls } = fakeDb();
    await store.write(caller, WS, "createService", null, {
      name: "Svc",
      keywords: [" CRM ", "crm"],
    });
    expect(JSON.parse(calls[0]?.init.body as string)).toMatchObject({ p_keywords: ["crm"] });
    await store.write(caller, WS, "inviteContributor", null, { email: "USER@example.com" });
    expect(JSON.parse(calls[1]?.init.body as string)).toMatchObject({
      p_email: "user@example.com",
    });
  });
  it("uses explicit connection columns and computes progress without selecting tokens", async () => {
    const requests: URL[] = [];
    vi.stubGlobal("fetch", (input: string | URL | Request) => {
      const url = new URL(input instanceof Request ? input.url : input.toString());
      requests.push(url);
      if (url.pathname.endsWith("/connection"))
        return Promise.resolve(Response.json([{ id: ID, owner_contributor_id: ID }]));
      if (url.pathname.endsWith("/contributor"))
        return Promise.resolve(Response.json([{ id: ID, email: "synthetic@example.com" }]));
      return Promise.resolve(
        Response.json([
          { connection_id: ID, status: "succeeded" },
          { connection_id: ID, status: "failed" },
          { connection_id: ID_B, status: "succeeded" },
        ]),
      );
    });
    const store = new SupabaseSetupStore("http://localhost:54321", key);
    expect(await store.read(caller, WS, "connections")).toMatchObject({
      items: [
        { imports: { total: 2, done: 1, failed: 1 }, contributor_email: "synthetic@example.com" },
      ],
    });
    for (const url of requests) expect(url.searchParams.get("workspace_id")).toBe(`eq.${WS}`);
    const columns = requests
      .find((url) => url.pathname.endsWith("/connection"))
      ?.searchParams.get("select");
    expect(columns).not.toContain("token");
    expect(columns).not.toContain("*");
  });
});

describe("setup metadata parity", () => {
  it.each([
    [[], "waiting"],
    [["succeeded", "succeeded"], "ready"],
    [["succeeded", "running"], "in_progress"],
    [["succeeded", "failed"], "needs_attention"],
  ] as const)(
    "summarizes active connections and mapped active clients (%s)",
    async (statuses, expected) => {
      const requests: URL[] = [];
      vi.stubGlobal("fetch", (input: string | URL | Request) => {
        const url = new URL(input instanceof Request ? input.url : input.toString());
        requests.push(url);
        const table = url.pathname.split("/").at(-1);
        const rows: Record<string, unknown> = {
          workspace: { id: WS, name: "Synthetic", timezone: "UTC", default_locale: "en" },
          client: [
            { id: ID, status: "active", policy: "full" },
            { id: "meta", status: "active", policy: "metadata_only" },
            { id: "excluded", status: "active", policy: "excluded" },
            { id: "prospect", status: "prospect", policy: "full" },
            { id: "unmapped", status: "active", policy: "full" },
          ],
          client_domain: [ID, "meta", "excluded", "prospect"].map((client_id) => ({ client_id })),
          connection: [
            { id: ID, status: "active" },
            { id: ID_B, status: "revoked" },
          ],
          job: [
            ...statuses.map((status) => ({ connection_id: ID, status })),
            { connection_id: ID_B, status: "failed" },
          ],
          llm_budget_event: [{ threshold: 100 }],
        };
        return Promise.resolve(Response.json(rows[table ?? ""]));
      });
      const store = new SupabaseSetupStore("http://localhost:54321", key);
      expect(await store.read(caller, WS, "setup")).toMatchObject({
        budget_threshold: 100,
        summary: {
          active_connections: 1,
          mapped_clients: 2,
          full_clients: 1,
          metadata_clients: 1,
          excluded_clients: 1,
          imports_total: statuses.length,
          imports_failed: statuses.filter((status) => status === "failed").length,
          import_status: expected,
        },
      });
      const budget = requests.find((url) => url.pathname.endsWith("llm_budget_event"));
      expect(budget?.searchParams.get("month")).toBe(
        `eq.${new Date().toISOString().slice(0, 7)}-01`,
      );
      expect(budget?.searchParams.get("order")).toBe("threshold.desc");
      expect(budget?.searchParams.get("limit")).toBe("1");
    },
  );
  it("keeps audit pagination bounded and exposes only safe event columns", async () => {
    let requested: URL | undefined;
    vi.stubGlobal("fetch", (input: string | URL | Request) => {
      requested = new URL(input instanceof Request ? input.url : input.toString());
      return Promise.resolve(
        Response.json(Array.from({ length: 51 }, (_, index) => ({ id: index }))),
      );
    });
    const store = new SupabaseSetupStore("http://localhost:54321", key);
    expect(await store.read(caller, WS, "audit", undefined, 2)).toMatchObject({ next_page: 3 });
    expect(requested?.searchParams.get("offset")).toBe("50");
    expect(requested?.searchParams.get("limit")).toBe("51");
    expect(requested?.searchParams.get("order")).toBe("created_at.desc,id.desc");
    const selected = requested?.searchParams.get("select") ?? "";
    for (const privateColumn of ["metadata", "ip", "user_agent"])
      expect(selected).not.toContain(privateColumn);
    expect(await store.read(caller, WS, "audit", undefined, 200)).toMatchObject({
      next_page: null,
    });
  });
});

describe("best-effort code invite email", () => {
  const invite = { email: "synthetic@example.com", workspace: "Synthetic", token: TOKEN };
  it("does not send when unconfigured, and tolerates network/provider failure", async () => {
    const send = vi.fn(() => Promise.resolve(Response.json({}, { status: 503 })));
    expect(await inviteEmailSender(undefined, undefined, send)(invite)).toBe(false);
    expect(send).not.toHaveBeenCalled();
    expect(await inviteEmailSender("fake-key", "test@example.com", send)(invite)).toBe(false);
    const fail = vi.fn(() => Promise.reject(new Error("network down")));
    expect(await inviteEmailSender("fake-key", "test@example.com", fail)(invite)).toBe(false);
  });
  it("never lets line breaks or control characters of a workspace name into the headers", async () => {
    const send = vi.fn((_input: string | URL | Request, _init?: RequestInit) =>
      Promise.resolve(Response.json({ id: ID })),
    );
    const hostile = { ...invite, workspace: "Acme\r\nBcc: victim@example.com\u0007" };
    expect(await inviteEmailSender("fake-key", "test@example.com", send)(hostile)).toBe(true);
    const sent = JSON.parse(send.mock.calls[0]?.[1]?.body as string) as { subject: string };
    expect(sent.subject).toBe("You're invited to join Acme Bcc: victim@example.com on Firra");
    expect(sent.subject).not.toMatch(/[\r\n]/);
  });
  it("sends the code once in English without a URL, with a 10s timeout", async () => {
    const timeout = vi.spyOn(AbortSignal, "timeout");
    const send = vi.fn((_input: string | URL | Request, init?: RequestInit) => {
      expect(init?.signal).toBeInstanceOf(AbortSignal);
      return Promise.resolve(Response.json({ id: ID }));
    });
    expect(await inviteEmailSender("fake-key", "test@example.com", send)(invite)).toBe(true);
    const sent = JSON.parse(send.mock.calls[0]?.[1]?.body as string) as {
      subject: string;
      text: string;
    };
    expect(sent.subject).toBe("You're invited to join Synthetic on Firra");
    expect(sent.text).toContain('Open Firra for Mac, sign in, choose "Join"');
    expect(sent.text.split(TOKEN)).toHaveLength(2);
    expect(sent.text).not.toMatch(/https?:|firra:\/\//);
    expect(timeout).toHaveBeenCalledWith(10_000);
    timeout.mockRestore();
  });
  it.each(["inviteAdmin", "inviteContributor"] as const)(
    "%s sends a code and ignores locale without returning a link",
    async (operation) => {
      fakeDb();
      const send = vi.fn(() => Promise.resolve(true));
      const store = new SupabaseSetupStore("http://localhost:54321", key, send);
      const response = await store.write(caller, WS, operation, null, {
        email: "USER@example.com",
        locale: "de",
      });
      expect(response).toMatchObject({ id: ID, token: TOKEN });
      expect(response).not.toHaveProperty("link");
      expect(send).toHaveBeenCalledWith({
        email: "user@example.com",
        workspace: "Synthetic workspace",
        token: TOKEN,
      });
      send.mockRejectedValueOnce(new Error("synthetic mail failure"));
      const failedMail = await store.write(caller, WS, operation, null, {
        email: "USER@example.com",
        locale: "fr",
      });
      expect(failedMail).toMatchObject({ token: TOKEN });
      expect(failedMail).not.toHaveProperty("link");
    },
  );
});

import {
  attentionResponse,
  changesResponse,
  clientsResponse,
  clientDetailResponse,
  timelineResponse,
  decisionsResponse,
  mcpClientChange,
  mcpCommitments,
  mcpStakeholders,
  mcpUnknowns,
  mcpEvidence,
  mcpReplay,
} from "@firra/contracts";
import { describe, expect, it, vi } from "vitest";
import { SupabaseReadStore } from "../src/lib/read-store.js";
import { buildClientDetail, buildClientListItem } from "../src/lib/clients.js";
import { UNTRUSTED_NOTE } from "../src/mcp/server.js";
import { WS, CLIENT, PARTS, ASSESSMENT, required } from "./client-fixtures.js";
import { RUN, RUN_ID, NARRATIVE } from "./replay-fixtures.js";
import { testApp, USERS } from "./helpers.js";
import { INJECTION, mcpRequest, mcpPayload } from "./mcp-tool-helpers.js";

const TOKEN = {
  id: "33333333-3333-4333-8333-333333333333",
  workspaceId: WS,
  userId: required(USERS["token-owner"]).id,
  scopes: ["read"],
};
const SECRET = `frm_${"ab".repeat(32)}`;
const FOREIGN = "44444444-4444-4444-8444-444444444444";
const EXCLUDED = "55555555-5555-4555-8555-555555555555";
const META = "66666666-6666-4666-8666-666666666666";
const detail = buildClientDetail(CLIENT, ASSESSMENT, [ASSESSMENT], PARTS);
const metadata = buildClientDetail(
  { ...CLIENT, id: META, policy: "metadata_only" },
  ASSESSMENT,
  [],
  PARTS,
);
const evidence = {
  id: TOKEN.id,
  client_id: CLIENT.id,
  quote: INJECTION,
  locator: { start: 0, end: INJECTION.length },
  deep_link: "https://example.com/synthetic",
  occurred_at: "2026-10-01T00:00:00Z",
  subject: "Synthetic",
  source_ref: "12345678",
  author_name: null,
  author_role: null,
};
function setup() {
  const store = new SupabaseReadStore("http://localhost:54321", "fixture");
  const detailRead = vi
    .spyOn(store, "clientDetail")
    .mockImplementation((_c, ws, id) =>
      Promise.resolve(
        ws === WS ? (id === CLIENT.id ? detail : id === META ? metadata : null) : null,
      ),
    );
  const reads = {
    attention: vi.spyOn(store, "attention").mockResolvedValue([]),
    changes: vi.spyOn(store, "changes").mockResolvedValue([]),
    clients: vi
      .spyOn(store, "clients")
      .mockResolvedValue([
        buildClientListItem(CLIENT, ASSESSMENT),
        buildClientListItem({ ...CLIENT, id: META, policy: "metadata_only" }, ASSESSMENT),
      ]),
    clientDetail: detailRead,
    timeline: vi
      .spyOn(store, "timeline")
      .mockImplementation((_c, ws, id) =>
        Promise.resolve(
          ws === WS && [CLIENT.id, META].includes(id) ? { items: [], next_cursor: null } : null,
        ),
      ),
    clientEvidence: vi
      .spyOn(store, "clientEvidence")
      .mockImplementation((_c, ws, id) =>
        Promise.resolve(
          ws === WS && [CLIENT.id, META].includes(id)
            ? { items: id === META ? [] : [evidence], next_cursor: null }
            : null,
        ),
      ),
    replayRuns: vi.spyOn(store, "replayRuns").mockResolvedValue([RUN]),
    replayClient: vi.spyOn(store, "replayClient").mockImplementation((_c, ws, run, id, now) =>
      Promise.resolve(
        ws === WS && run === RUN_ID && [CLIENT.id, META].includes(id)
          ? {
              generated_at: now.toISOString(),
              run_id: run,
              client_id: id,
              version: 1,
              narrative:
                id === META
                  ? {
                      ...NARRATIVE,
                      transitions: [],
                      events: [],
                      visible_at_detection: [],
                      detection_timing: [],
                    }
                  : NARRATIVE,
              weeks: [],
              evidence_ids: id === META ? [] : [TOKEN.id],
            }
          : null,
      ),
    ),
    decisions: vi.spyOn(store, "decisions").mockResolvedValue({ items: [], next_cursor: null }),
  };
  const writes = {
    feedback: vi.spyOn(store.writes, "feedback").mockRejectedValue(new Error("unexpected write")),
    outcome: vi.spyOn(store.writes, "outcome").mockRejectedValue(new Error("unexpected write")),
    deleteOutcome: vi
      .spyOn(store.writes, "deleteOutcome")
      .mockRejectedValue(new Error("unexpected write")),
  };
  const errors: unknown[] = [];
  const mint = vi.fn(() => Promise.resolve("minted-session"));
  const t = testApp({
    readStore: store,
    mcpTokenStore: { resolve: () => Promise.resolve(TOKEN) },
    sessionMinter: { mint },
    captureException: (e) => errors.push(e),
  });
  t.repo.addMember(WS, TOKEN.userId, "owner");
  return { ...t, reads, writes, mint, errors };
}
const cases = [
  ["get_clients_needing_attention", {}, attentionResponse, "attention", 0],
  ["list_clients", {}, clientsResponse, "clients", 2],
  ["get_client_state", { client_id: CLIENT.id }, clientDetailResponse, "clientDetail", 1],
  ["explain_client_change", { client_id: CLIENT.id }, mcpClientChange, "clientDetail", 1],
  ["get_open_commitments", { client_id: CLIENT.id }, mcpCommitments, "clientDetail", 1],
  ["get_stakeholders", { client_id: CLIENT.id }, mcpStakeholders, "clientDetail", 1],
  ["get_client_unknowns", { client_id: CLIENT.id }, mcpUnknowns, "clientDetail", 1],
  ["get_client_timeline", { client_id: CLIENT.id }, timelineResponse, "timeline", 0],
  ["search_evidence", { client_id: CLIENT.id }, mcpEvidence, "clientEvidence", 1],
  ["get_churn_replay", {}, mcpReplay, "replayRuns", 1],
  ["get_churn_replay", { client_id: CLIENT.id, run_id: RUN_ID }, mcpReplay, "replayClient", 1],
  ["get_decisions", {}, decisionsResponse, "decisions", 0],
  ["get_recent_changes", {}, changesResponse, "changes", 0],
] as const;
for (const [name, args, schema, method, count] of cases)
  describe(`${name} ${method}`, () => {
    it("returns validated stored data as token user, with exactly one targeted MCP audit and no row logs", async () => {
      const t = setup();
      const res = await t.app.request("/mcp", mcpRequest(SECRET, name, args));
      expect(res.status).toBe(200);
      const payload = await mcpPayload(res);
      expect(payload.result?.isError).not.toBe(true);
      schema.parse(payload.result?.structuredContent);
      expect(t.reads[method]).toHaveBeenCalledOnce();
      expect(t.reads[method].mock.calls[0]?.slice(0, 2)).toEqual([
        { token: "minted-session", userId: TOKEN.userId },
        WS,
      ]);
      expect(t.mint).toHaveBeenCalledOnce();
      expect(t.repo.audit).toEqual([
        expect.objectContaining({
          workspaceId: WS,
          actorType: "mcp",
          actorId: TOKEN.id,
          action: `mcp.${name}`,
          metadata: { count },
          ...("client_id" in args ? { targetType: "client", targetId: CLIENT.id } : {}),
        }),
      ]);
      expect(t.logs.join()).not.toMatch(/Synthetic|Ignore previous|minted-session/);
    });
    it("reports store and audit failures once and returns no data", async () => {
      for (const failStore of [true, false]) {
        const t = setup();
        if (failStore) t.reads[method].mockRejectedValue(new Error("PRIVATE ROW DATA"));
        else t.repo.failAudit = true;
        const payload = await mcpPayload(
          await t.app.request("/mcp", mcpRequest(SECRET, name, args)),
        );
        expect(payload.result).toEqual({
          isError: true,
          content: [{ type: "text", text: "Firra could not complete this request." }],
        });
        expect(t.errors).toHaveLength(1);
        expect(t.repo.audit).toHaveLength(0);
        expect(t.logs.join()).not.toContain("PRIVATE ROW DATA");
      }
    });
    if ("client_id" in args) {
      it("does not distinguish missing, foreign and excluded ids and writes no audit", async () => {
        const t = setup();
        for (const id of [crypto.randomUUID(), FOREIGN, EXCLUDED]) {
          const body = await mcpPayload(
            await t.app.request("/mcp", mcpRequest(SECRET, name, { ...args, client_id: id })),
          );
          expect(body.result).toEqual({
            isError: true,
            content: [{ type: "text", text: "Client not found." }],
          });
        }
        expect(t.repo.audit).toHaveLength(0);
        expect(t.errors).toHaveLength(0);
      });
      it("exposes no quote for metadata-only clients", async () => {
        const t = setup();
        const body = await mcpPayload(
          await t.app.request("/mcp", mcpRequest(SECRET, name, { ...args, client_id: META })),
        );
        schema.parse(body.result?.structuredContent);
        expect(JSON.stringify(body)).not.toContain("untrusted_quote");
        expect(JSON.stringify(body)).not.toContain(INJECTION);
        expect(t.repo.audit[0]?.targetId).toBe(META);
      });
    }
    it("rejects a forged workspace argument before invoking any read", async () => {
      const t = setup();
      const body = await mcpPayload(
        await t.app.request("/mcp", mcpRequest(SECRET, name, { ...args, workspace_id: FOREIGN })),
      );
      expect(body.error ?? body.result?.isError).toBeTruthy();
      expect(t.mint).not.toHaveBeenCalled();
      expect(t.repo.audit).toHaveLength(0);
    });
  });
it("contains injection as unchanged third-party data and performs no extra tool or write", async () => {
  const t = setup();
  const body = await mcpPayload(
    await t.app.request("/mcp", mcpRequest(SECRET, "search_evidence", { client_id: CLIENT.id })),
  );
  expect(mcpEvidence.parse(body.result?.structuredContent).items[0]?.untrusted_quote).toBe(
    INJECTION,
  );
  expect(body.result?.content?.[0]?.text).toContain(
    `<untrusted_quote>${INJECTION}</untrusted_quote>`,
  );
  expect(JSON.stringify(body.result?.structuredContent)).not.toContain('"quote":');
  expect(t.reads.clientEvidence).toHaveBeenCalledOnce();
  expect(t.reads.decisions).not.toHaveBeenCalled();
  expect(Object.values(t.reads).reduce((n, read) => n + read.mock.calls.length, 0)).toBe(1);
  for (const write of Object.values(t.writes)) expect(write).not.toHaveBeenCalled();
  expect(t.repo.audit).toHaveLength(1);
  expect(t.repo.audit[0]?.action).toBe("mcp.search_evidence");
});
it("get_decisions passes importance and kind to the store and refuses unknown values", async () => {
  const t = setup();
  const ok = await mcpPayload(
    await t.app.request(
      "/mcp",
      mcpRequest(SECRET, "get_decisions", { importance: "important", kind: "state_change" }),
    ),
  );
  expect(ok.result?.isError).not.toBe(true);
  expect(t.reads.decisions.mock.calls[0]?.[5]).toEqual({
    importance: "important",
    kind: "state_change",
  });
  t.reads.decisions.mockClear();
  for (const args of [{ importance: "urgent" }, { kind: "bogus" }, { importance: 1 }]) {
    const refused = await mcpPayload(
      await t.app.request("/mcp", mcpRequest(SECRET, "get_decisions", args)),
    );
    expect(refused.error ?? refused.result?.isError).toBeTruthy();
  }
  expect(t.reads.decisions).not.toHaveBeenCalled();
});
for (const era of ["2025-11-25", "2026-07-28"])
  it(`lists conforming read-only tools over ${era}`, async () => {
    const t = setup();
    const result = await mcpPayload(
      await t.app.request("/mcp", mcpRequest(SECRET, "tools/list", {}, era)),
    );
    const tools = result.result?.tools ?? [];
    expect(tools.map((tool) => tool.name).sort()).toEqual(
      [...new Set(cases.map(([name]) => name))].sort(),
    );
    for (const tool of tools) {
      expect(tool.annotations?.readOnlyHint).toBe(true);
      expect(tool.inputSchema.type).toBe("object");
      expect(tool.inputSchema.additionalProperties).toBe(false);
      expect(JSON.stringify(tool.inputSchema)).not.toContain("workspace_id");
      expect(tool.outputSchema).toBeDefined();
      if (tool.name === "search_evidence")
        expect(tool.description?.endsWith(UNTRUSTED_NOTE)).toBe(true);
    }
    expect(t.repo.audit).toHaveLength(0);
    expect(t.mint).not.toHaveBeenCalled();
  });
it("validates ids, pagination and paired replay arguments before reads", async () => {
  const t = setup();
  for (const [name, args] of [
    ["get_client_state", { client_id: "bad" }],
    ["search_evidence", { client_id: CLIENT.id, limit: 101 }],
    ["get_churn_replay", { client_id: CLIENT.id }],
    ["search_evidence", { client_id: CLIENT.id, cursor: "bogus" }],
  ] as const) {
    const body = await mcpPayload(await t.app.request("/mcp", mcpRequest(SECRET, name, args)));
    expect(body.error ?? body.result?.isError).toBeTruthy();
  }
  expect(t.mint).not.toHaveBeenCalled();
  expect(t.repo.audit).toHaveLength(0);
});

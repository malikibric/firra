/**
 * T14 requirements 9 and 10: MCP token binding, argument handling, revoked/expired tokens, one
 * audit row per successful call, no argument text in audit, and the untrusted-quote contract.
 * Expectations come from `CLAUDE.md` §6 (untrusted content, audit, logs) and milestone F evals.
 */
import { describe, expect, it } from "vitest";
import { INJECTION, mcpPayload, mcpRequest } from "../mcp-tool-helpers.js";
import {
  A_SECRET,
  CA,
  CB,
  CMA,
  CONTRIBUTOR_SECRET,
  CXA,
  EXPIRED_SECRET,
  NAME_CA,
  REVOKED_SECRET,
  WA,
  WB,
  emailLeaks,
  secretLeaks,
  world,
} from "./fixtures.js";

const CLIENT_TOOLS = [
  "get_client_state",
  "explain_client_change",
  "get_open_commitments",
  "get_stakeholders",
  "get_client_unknowns",
  "get_client_timeline",
  "search_evidence",
] as const;

describe("requirement 9: MCP surface", () => {
  it("a forged workspace_id is rejected before the tool runs", async () => {
    const t = world();
    for (const name of [
      "list_clients",
      "get_decisions",
      "get_clients_needing_attention",
    ] as const) {
      const payload = await mcpPayload(
        await t.app.request("/mcp", mcpRequest(A_SECRET, name, { workspace_id: WB })),
      );
      expect(payload.error ?? payload.result?.isError, `${name} workspace`).toBeTruthy();
    }
    expect(t.mint).not.toHaveBeenCalled();
    expect(t.repo.audit).toEqual([]);
  });

  it("a foreign or excluded client_id never resolves, and only the token workspace is read", async () => {
    const t = world();
    for (const name of CLIENT_TOOLS) {
      for (const clientId of [CB, CXA]) {
        const payload = await mcpPayload(
          await t.app.request("/mcp", mcpRequest(A_SECRET, name, { client_id: clientId })),
        );
        expect(payload.result).toEqual({
          isError: true,
          content: [{ type: "text", text: "Client not found." }],
        });
      }
    }
    expect(t.repo.audit).toEqual([]); // a client that does not resolve is never audited
    for (const call of t.reads.clientDetail.mock.calls) expect(call[1]).toBe(WA); // reads run against the token's workspace, never WB
  });

  it("unknown extra arguments are rejected before any read", async () => {
    const t = world();
    for (const args of [
      { client_id: CA, extra: 1 },
      { client_id: CA, workspace_id: WA },
    ]) {
      const payload = await mcpPayload(
        await t.app.request("/mcp", mcpRequest(A_SECRET, "get_client_state", args)),
      );
      expect(payload.error ?? payload.result?.isError).toBeTruthy();
    }
    expect(t.mint).not.toHaveBeenCalled();
    expect(t.repo.audit).toEqual([]);
  });

  it("refuses a revoked, expired, wrong-role or missing token, and never audits", async () => {
    const t = world();
    const first = await mcpPayload(
      await t.app.request("/mcp", mcpRequest(A_SECRET, "list_clients", {})),
    );
    expect(first.result?.isError).not.toBe(true);
    t.mcp.revoke(A_SECRET); // revoked: the very next call must fail
    const refused = await mcpPayload(
      await t.app.request("/mcp", mcpRequest(A_SECRET, "list_clients", {})),
    );
    expect(refused.result?.isError ?? refused.error).toBeTruthy();
    t.repo.audit.length = 0;
    for (const secret of [REVOKED_SECRET, EXPIRED_SECRET, CONTRIBUTOR_SECRET, "not-a-token"]) {
      const res = await t.app.request("/mcp", mcpRequest(secret, "list_clients", {}));
      expect(res.status, secret).toBe(401);
    }
    const anonymous = await t.app.request("/mcp", mcpRequest("", "list_clients", {}));
    expect(anonymous.status).toBe(401);
    expect(t.repo.audit).toEqual([]);
  });

  it("writes exactly one audit row per successful call and none per refusal", async () => {
    const t = world();
    const calls = [
      ["get_clients_needing_attention", {}],
      ["list_clients", {}],
      ["search_evidence", { client_id: CA }],
    ] as const;
    for (const [name, args] of calls) {
      const payload = await mcpPayload(
        await t.app.request("/mcp", mcpRequest(A_SECRET, name, args)),
      );
      expect(payload.result?.isError).not.toBe(true);
    }
    expect(t.repo.audit).toHaveLength(3);
    for (const row of t.repo.audit) {
      expect(row.actorType).toBe("mcp");
      expect(row.workspaceId).toBe(WA);
      expect(row.action.startsWith("mcp.")).toBe(true);
      expect(Object.keys(row.metadata)).toEqual(["count"]);
    }
    // A refused argument adds no audit row and carries no argument text.
    await t.app.request("/mcp", mcpRequest(A_SECRET, "get_client_state", { client_id: WB }));
    expect(t.repo.audit).toHaveLength(3);
    expect(JSON.stringify(t.repo.audit)).not.toContain(INJECTION);
    expect(JSON.stringify(t.repo.audit)).not.toContain(NAME_CA);
  });

  it("keeps the injected quote as data inside untrusted_quote only", async () => {
    const t = world();
    const payload = await mcpPayload(
      await t.app.request("/mcp", mcpRequest(A_SECRET, "search_evidence", { client_id: CA })),
    );
    const structured = payload.result?.structuredContent as {
      items: { untrusted_quote: string }[];
    };
    expect(structured.items[0]?.untrusted_quote).toBe(INJECTION);
    expect(JSON.stringify(payload.result?.structuredContent)).not.toContain('"quote":');
    expect(payload.result?.content?.[0]?.text).toContain(
      `<untrusted_quote>${INJECTION}</untrusted_quote>`,
    );
    // The injection is data: it never triggers another tool and no write happens.
    expect(t.reads.clientEvidence).toHaveBeenCalledOnce();
    expect(t.reads.decisions).not.toHaveBeenCalled();
    expect(t.writes.calls).toEqual([]);
  });

  it("exposes no email field for a metadata_only client and no secret anywhere", async () => {
    const t = world();
    for (const name of ["get_client_state", "search_evidence", "get_client_timeline"] as const) {
      const payload = await mcpPayload(
        await t.app.request("/mcp", mcpRequest(A_SECRET, name, { client_id: CMA })),
      );
      expect(payload.result?.isError).not.toBe(true);
      expect(emailLeaks(payload), name).toEqual([]);
      expect(secretLeaks(payload), name).toEqual([]);
      expect(JSON.stringify(payload)).not.toContain(INJECTION);
    }
  });

  it("logs never contain the quote, the minted session or a client name", async () => {
    const t = world();
    await t.app.request("/mcp", mcpRequest(A_SECRET, "search_evidence", { client_id: CA }));
    await t.app.request("/mcp", mcpRequest(A_SECRET, "list_clients", {}));
    const logs = t.logs.join("\n");
    for (const secret of [INJECTION, "minted-session", NAME_CA, A_SECRET]) {
      expect(logs).not.toContain(secret);
    }
  });
});

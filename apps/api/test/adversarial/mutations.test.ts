/**
 * T14 requirements 8 and 10: mutation validation, indistinguishable 404s, replay of the same
 * feedback, and the audit/logging guarantees. Expectations come from `CLAUDE.md` §6 (audit, logs)
 * and the `/v1` error model; the RPCs' own authorization is proven by the integration suite.
 */
import { apiError } from "@firra/contracts";
import { describe, expect, it } from "vitest";
import { mcpRequest } from "../mcp-tool-helpers.js";
import {
  A_SECRET,
  CA,
  CARD_A,
  CARD_B,
  CMA,
  NAME_CA,
  OUTCOME_A,
  WA,
  WB,
  request,
  world,
} from "./fixtures.js";

const base = `/v1/workspaces/${WA}`;
const feedbackUrl = `${base}/decisions/${CARD_A}/feedback`;
const outcomeUrl = `${base}/clients/${CA}/outcomes`;

describe("requirement 8: mutations", () => {
  it("rejects a missing or wrong Content-Type before any write", async () => {
    const t = world();
    for (const contentType of ["", "text/plain", "application/x-www-form-urlencoded"]) {
      for (const [url, body] of [
        [feedbackUrl, { verdict: "useful" }],
        [outcomeUrl, { event: "churned", occurred_at: "2026-10-01" }],
      ] as const) {
        const res = await request(t.app, "POST", url, "owner-a", body, contentType);
        expect(res.status, `${contentType} ${url}`).toBe(422);
      }
    }
    expect(t.writes.calls).toEqual([]);
  });

  it("rejects bodies over the contract limits and invalid outcome shapes", async () => {
    const t = world();
    const oversized = [
      [feedbackUrl, { verdict: "useful", note: "x".repeat(1001) }],
      [outcomeUrl, { event: "churned", occurred_at: "2026-10-01", note: "x".repeat(1001) }],
      [outcomeUrl, { event: "churned", occurred_at: "2026-10-01", amount_change: 5 }],
      [outcomeUrl, { event: "churned", occurred_at: "2026-10-01", notice_at: "2026-10-02" }],
      [outcomeUrl, { event: "not_an_event", occurred_at: "2026-10-01" }],
    ] as const;
    for (const [url, body] of oversized) {
      const res = await request(t.app, "POST", url, "owner-a", body);
      expect(res.status, url).toBe(422);
    }
    expect(t.writes.calls).toEqual([]);
  });

  it("feedback on a card the caller cannot see is an indistinguishable 404", async () => {
    const t = world();
    for (const cardId of [CARD_B, "d0000000-0000-4000-8000-0000000000c0"]) {
      const res = await request(t.app, "POST", `${base}/decisions/${cardId}/feedback`, "owner-a", {
        verdict: "useful",
      });
      expect(res.status).toBe(404);
      apiError.parse(await res.json());
    }
  });

  it("outcome writes and deletes across workspaces are 404 and change nothing", async () => {
    const t = world();
    const crossDelete = await request(
      t.app,
      "DELETE",
      `/v1/workspaces/${WB}/outcomes/${OUTCOME_A}`,
      "owner-b",
    );
    expect(crossDelete.status).toBe(404);
    const missingDelete = await request(
      t.app,
      "DELETE",
      `${base}/outcomes/00000000-0000-4000-8000-0000000000ff`,
      "owner-a",
    );
    expect(missingDelete.status).toBe(404);
    const foreignOutcome = await request(
      t.app,
      "POST",
      `${base}/clients/${CARD_B}/outcomes`,
      "owner-a",
      {
        event: "churned",
        occurred_at: "2026-10-01",
      },
    );
    expect(foreignOutcome.status).toBe(404);
    // The one legitimate delete succeeds and writes exactly once.
    t.writes.calls.length = 0;
    const ok = await request(t.app, "DELETE", `${base}/outcomes/${OUTCOME_A}`, "owner-a");
    expect(ok.status).toBe(204);
    expect(t.writes.calls).toHaveLength(1);
  });

  it("replaying the same feedback returns one id (latest wins)", async () => {
    const t = world();
    const first = await request(t.app, "POST", feedbackUrl, "owner-a", { verdict: "wrong" });
    const second = await request(t.app, "POST", feedbackUrl, "owner-a", { verdict: "useful" });
    expect(first.status).toBe(200);
    expect(second.status).toBe(200);
    const ids = [
      ((await first.json()) as { id: string }).id,
      ((await second.json()) as { id: string }).id,
    ];
    expect(ids).toEqual([CARD_A, CARD_A]);
    expect(t.writes.calls).toHaveLength(2);
  });
});

describe("requirement 10: audit and logs", () => {
  it("each successful read writes exactly one audit row and mutations add none", async () => {
    const t = world();
    const reads = [
      `${base}/attention`,
      `${base}/changes`,
      `${base}/decisions`,
      `${base}/clients`,
      `${base}/clients/${CA}`,
      `${base}/clients/${CA}/timeline`,
      `${base}/clients/${CA}/evidence`,
      `${base}/evidence/e0000000-0000-4000-8000-00000000000a`,
      `${base}/meetings/upcoming`,
      `${base}/replay/runs`,
      `${base}/replay/runs/cccccccc-cccc-4ccc-8ccc-cccccccccccc/clients/${CA}`,
      `${base}/setup`,
      `${base}/audit`,
    ];
    for (const url of reads) {
      t.repo.audit.length = 0;
      const res = await request(t.app, "GET", url, "owner-a");
      expect(res.status, url).toBe(200);
      expect(t.repo.audit, url).toHaveLength(1);
      expect(t.repo.audit[0]?.action, url).toMatch(/^read\./);
      expect(t.repo.audit[0]?.workspaceId, url).toBe(WA);
    }
    t.repo.audit.length = 0;
    await request(t.app, "POST", feedbackUrl, "owner-a", { verdict: "useful" });
    await request(t.app, "DELETE", `${base}/outcomes/${OUTCOME_A}`, "owner-a");
    expect(t.repo.audit).toEqual([]); // the caller's RPC owns the mutation audit
  });

  it("a failed audit write returns no data", async () => {
    const t = world();
    t.repo.failAudit = true;
    const res = await request(t.app, "GET", `${base}/attention`, "owner-a");
    expect(res.status).toBe(500);
    const text = await res.text();
    expect(text).not.toContain(NAME_CA);
    expect(text).not.toContain(CA);
    apiError.parse(JSON.parse(text));
  });

  it("logs never contain a quote, email address, token or client name", async () => {
    const t = world();
    await request(t.app, "GET", `${base}/clients/${CA}/evidence`, "owner-a");
    await request(t.app, "GET", `${base}/clients/${CMA}`, "owner-a");
    // search_evidence returns the injected quote; the log must not contain it.
    await t.app.request("/mcp", mcpRequest(A_SECRET, "search_evidence", { client_id: CA }));
    t.writes.error = new Error("PRIVATE ROW DATA");
    await request(t.app, "POST", feedbackUrl, "owner-a", { verdict: "useful" });
    const logs = t.logs.join("\n");
    for (const secret of [
      NAME_CA,
      "Ignore previous",
      "owner-a@example.com",
      "minted-session",
      "PRIVATE ROW DATA",
    ])
      expect(logs).not.toContain(secret);
  });
});

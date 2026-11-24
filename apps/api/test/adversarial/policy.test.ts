/**
 * T14 requirements 4, 5 and 6: `excluded` is absent, `metadata_only` never exposes an email field,
 * and no response leaks a secret or internal column. Expectations come from `CLAUDE.md` §2 rule 8
 * and §6; the real policy enforcement in Postgres is proven by the integration suite.
 */
import { describe, expect, it } from "vitest";
import { INJECTION, mcpPayload, mcpRequest } from "../mcp-tool-helpers.js";
import { RUN_ID } from "../replay-fixtures.js";
import {
  A_SECRET,
  CA,
  CMA,
  CXA,
  NAME_CA,
  NAME_CXA,
  WA,
  emailLeaks,
  request,
  secretLeaks,
  world,
} from "./fixtures.js";

const base = `/v1/workspaces/${WA}`;
const CARD_EXCLUDED = "d0000000-0000-4000-8000-00000000000c";
const LISTS = ["/attention", "/clients", "/changes", "/decisions", "/meetings/upcoming"] as const;

describe("requirement 4: excluded clients", () => {
  it("are absent from every list", async () => {
    const t = world();
    for (const path of LISTS) {
      const res = await request(t.app, "GET", `${base}${path}`, "owner-a");
      expect(res.status, path).toBe(200);
      const text = await res.text();
      expect(text, path).not.toContain(CXA);
      expect(text, path).not.toContain(NAME_CXA);
    }
  });

  it("are 404 on detail, timeline, evidence, replay and both writes", async () => {
    const t = world();
    const reads = [
      `${base}/clients/${CXA}`,
      `${base}/clients/${CXA}/timeline`,
      `${base}/clients/${CXA}/evidence`,
      `${base}/replay/runs/${RUN_ID}/clients/${CXA}`,
    ];
    for (const url of reads) {
      const res = await request(t.app, "GET", url, "owner-a");
      expect(res.status, url).toBe(404);
      expect(await res.text()).not.toContain(NAME_CXA);
    }
    const writes: [string, string, unknown][] = [
      ["POST", `${base}/clients/${CXA}/outcomes`, { event: "churned", occurred_at: "2026-10-01" }],
      ["POST", `${base}/decisions/${CARD_EXCLUDED}/feedback`, { verdict: "useful" }],
    ];
    for (const [method, url, body] of writes) {
      const res = await request(t.app, method, url, "owner-a", body);
      expect(res.status, url).toBe(404);
    }
  });

  it("disappear from every read once the policy changes, cards and outcomes included", async () => {
    const t = world();
    expect(await (await request(t.app, "GET", `${base}/decisions`, "owner-a")).text()).toContain(
      NAME_CA,
    );
    t.restrict(CA); // the client became excluded
    for (const path of LISTS) {
      const text = await (await request(t.app, "GET", `${base}${path}`, "owner-a")).text();
      expect(text, path).not.toContain(NAME_CA);
      expect(text, path).not.toContain(CA);
    }
    expect((await request(t.app, "GET", `${base}/clients/${CA}`, "owner-a")).status).toBe(404);
    expect(
      (
        await request(t.app, "POST", `${base}/clients/${CA}/outcomes`, "owner-a", {
          event: "churned",
          occurred_at: "2026-10-01",
        })
      ).status,
    ).toBe(404);
  });
});

describe("requirement 5: metadata_only exposes no email field", () => {
  it("no quote, body_clean, raw_object_uri, deep_link or email text on any read", async () => {
    const t = world();
    const targets = [
      `${base}/clients/${CMA}`,
      `${base}/clients/${CMA}/timeline`,
      `${base}/clients/${CMA}/evidence`,
      `${base}/decisions`,
      `${base}/meetings/upcoming`,
      `${base}/replay/runs/${RUN_ID}/clients/${CMA}`,
    ];
    for (const url of targets) {
      const res = await request(t.app, "GET", url, "owner-a");
      expect(res.status, url).toBe(200);
      expect(emailLeaks(await res.json()), url).toEqual([]);
    }
  });

  it("the metadata evidence route is empty and the scanner still catches a real quote", async () => {
    const t = world();
    const meta = await request(t.app, "GET", `${base}/clients/${CMA}/evidence`, "owner-a");
    expect((await meta.json()) as { items: unknown[] }).toMatchObject({ items: [] });
    // Control: a full client's evidence exposes the quote, so an empty metadata page is meaningful.
    const full = await request(t.app, "GET", `${base}/clients/${CA}/evidence`, "owner-a");
    expect(emailLeaks(await full.json()).length).toBeGreaterThan(0);
  });

  it("the MCP evidence tool alters nothing into metadata output", async () => {
    const t = world();
    const payload = await mcpPayload(
      await t.app.request("/mcp", mcpRequest(A_SECRET, "search_evidence", { client_id: CMA })),
    );
    expect(JSON.stringify(payload)).not.toContain("untrusted_quote");
    expect(JSON.stringify(payload)).not.toContain(INJECTION);
    expect(emailLeaks(payload)).toEqual([]);
  });

  it("metadata decisions carry no evidence ids", async () => {
    const t = world();
    const body = (await (await request(t.app, "GET", `${base}/decisions`, "owner-a")).json()) as {
      items: { client_id: string; evidence_ids: string[] }[];
    };
    const metadata = body.items.find((item) => item.client_id === CMA);
    expect(metadata?.evidence_ids).toEqual([]);
  });
});

describe("requirement 6: no secret or internal column", () => {
  it("the scanner catches a planted secret", () => {
    expect(secretLeaks({ a: { token_hash: "x", nested: { wrapped_dek: "y" } } })).toEqual([
      "$.a.token_hash",
      "$.a.nested.wrapped_dek",
    ]);
    expect(secretLeaks({ ok: true })).toEqual([]);
  });

  it("no /v1 read or MCP result contains a secret key", async () => {
    const t = world();
    const urls = [
      `${base}/attention`,
      `${base}/changes`,
      `${base}/clients`,
      `${base}/clients/${CA}`,
      `${base}/decisions`,
      `${base}/meetings/upcoming`,
      `${base}/replay/runs`,
      `${base}/setup`,
      `${base}/audit`,
    ];
    for (const url of urls) {
      const res = await request(t.app, "GET", url, "owner-a");
      expect(secretLeaks(await res.json()), url).toEqual([]);
    }
    const toolArgs: Record<string, unknown>[] = [{}, { client_id: CA }];
    for (const args of toolArgs) {
      for (const name of [
        "get_clients_needing_attention",
        "list_clients",
        "get_client_state",
        "search_evidence",
        "get_churn_replay",
        "get_decisions",
      ]) {
        const payload = await mcpPayload(
          await t.app.request("/mcp", mcpRequest(A_SECRET, name, args)),
        );
        expect(secretLeaks(payload), `${name} ${JSON.stringify(args)}`).toEqual([]);
      }
    }
  });
});

/** Local Supabase only: real PAT resolution, minted caller sessions, RLS and audit. */
import { randomUUID } from "node:crypto";
import type { SupabaseClient } from "@supabase/supabase-js";
import { expect, it } from "vitest";
import { mcpEvidence } from "@firra/contracts";
import type { ReplayDatabase } from "../../src/lib/replay-schema.js";
import { SupabaseReadStore } from "../../src/lib/read-store.js";
import { SupabaseRepository } from "../../src/lib/repository.js";
import { SupabaseMcpTokenAdmin } from "../../src/mcp/admin.js";
import { SupabaseMcpTokenStore, SupabaseSessionMinter } from "../../src/mcp/tokens.js";
import { testApp } from "../helpers.js";
import { NARRATIVE } from "../replay-fixtures.js";
import { INJECTION, mcpRequest, mcpPayload } from "../mcp-tool-helpers.js";
import { admin, userWithToken, workspace, SUPABASE_URL, PUBLISHABLE_KEY } from "./read-helpers.js";

function checked<T>(result: { data: T; error: unknown }) {
  if (result.error) throw new Error("synthetic MCP fixture failed");
  return result.data;
}
it("binds every tool to the PAT workspace, hides excluded clients, and contains injected quotes", async () => {
  const workspaces: string[] = [];
  const users: string[] = [];
  try {
    const ws = await workspace();
    workspaces.push(ws);
    const foreignWs = await workspace();
    workspaces.push(foreignWs);
    const owner = await userWithToken("mcp-tools-owner");
    users.push(owner.id);
    // The minted user can see both workspaces: only the PAT's workspace may be read by MCP.
    checked(
      await admin.from("membership").insert([
        { workspace_id: ws, user_id: owner.id, role: "owner" },
        { workspace_id: foreignWs, user_id: owner.id, role: "owner" },
      ]),
    );
    const full = randomUUID(),
      metadata = randomUUID(),
      excluded = randomUUID(),
      foreign = randomUUID();
    checked(
      await admin.from("client").insert([
        { id: full, workspace_id: ws, name: "Synthetic Full", policy: "full" },
        { id: metadata, workspace_id: ws, name: "Synthetic Metadata", policy: "metadata_only" },
        { id: excluded, workspace_id: ws, name: "Synthetic Excluded", policy: "excluded" },
        { id: foreign, workspace_id: foreignWs, name: "Synthetic Foreign", policy: "full" },
      ]),
    );
    const today = new Date().toISOString().slice(0, 10);
    for (const client of [full, metadata, excluded, foreign]) {
      const workspace_id = client === foreign ? foreignWs : ws;
      checked(
        await admin.from("account_assessment").insert({
          workspace_id,
          client_id: client,
          as_of: today,
          run_kind: "live",
          assessment_version: "fixture",
          rule_version: "fixture",
          risk_state: "high",
          state_since: today,
          expansion_state: "none",
          strength: "moderate",
          play: "fix_first",
          drivers: [],
        }),
      );
      checked(
        await admin.from("decision_card").insert({
          workspace_id,
          client_id: client,
          week: "2026-09-28",
          kind: "risk",
          title: "Synthetic card",
          body: "Synthetic metadata facts",
          locale: "en",
          score: 1,
          priority: 1,
          inputs: {},
          model: "fixture",
          prompt_version: "fixture",
          dedupe_key: client,
          play: "fix_first",
          facts: [{ basis: "metadata" }],
          interpretation: "Synthetic interpretation",
          recommendation: "Synthetic recommendation",
          strength: "moderate",
        }),
      );
    }
    const connection = checked(
      await admin
        .from("connection")
        .insert({
          workspace_id: ws,
          provider: "email_upload",
          mode: "upload",
          account_label: "Synthetic upload",
        })
        .select("id")
        .single(),
    );
    if (!connection) throw new Error("fixture connection missing");
    const source = checked(
      await admin
        .from("source_record")
        .insert({
          workspace_id: ws,
          client_id: full,
          connection_id: connection.id,
          provider: "email_upload",
          external_id: randomUUID(),
          kind: "email",
          direction: "in",
          policy_at_ingest: "full",
          occurred_at: `${today}T00:00:00Z`,
          body_clean: INJECTION,
        })
        .select("id")
        .single(),
    );
    if (!source) throw new Error("fixture source missing");
    const evidence = checked(
      await admin
        .from("evidence")
        .insert({
          workspace_id: ws,
          client_id: full,
          source_record_id: source.id,
          quote: INJECTION,
          locator: { start: 0, end: INJECTION.length },
          deep_link: "https://example.com/synthetic",
        })
        .select("id")
        .single(),
    );
    if (!evidence) throw new Error("fixture evidence missing");
    const run = checked(
      await admin
        .from("replay_run")
        .insert({
          workspace_id: ws,
          window_start: "2026-09-01",
          window_end: "2026-09-30",
          status: "succeeded",
          finished_at: "2026-09-30T12:00:00Z",
        })
        .select("id")
        .single(),
    );
    if (!run) throw new Error("fixture replay missing");
    const replayDb = admin as unknown as SupabaseClient<ReplayDatabase>;
    for (const client of [full, metadata, excluded])
      checked(
        await replayDb.from("replay_narrative").insert({
          workspace_id: ws,
          replay_run_id: run.id,
          client_id: client,
          version: 1,
          narrative: {
            ...NARRATIVE,
            visible_at_detection: [],
            detection_timing: [],
            transitions: [],
            events: [],
          },
        }),
      );
    const tokenAdmin = new SupabaseMcpTokenAdmin(SUPABASE_URL, PUBLISHABLE_KEY);
    const pat = await tokenAdmin.create({ userId: owner.id, token: owner.token }, ws, {
      name: "synthetic",
      days: 1,
    });
    const store = new SupabaseReadStore(SUPABASE_URL, PUBLISHABLE_KEY);
    const app = testApp({
      repo: new SupabaseRepository(admin),
      readStore: store,
      mcpTokenStore: new SupabaseMcpTokenStore(admin),
      sessionMinter: new SupabaseSessionMinter(admin, SUPABASE_URL, PUBLISHABLE_KEY),
    }).app;
    const cases = [
      ["get_client_state", {}],
      ["explain_client_change", {}],
      ["get_open_commitments", {}],
      ["get_stakeholders", {}],
      ["get_client_unknowns", {}],
      ["get_client_timeline", {}],
      ["search_evidence", {}],
      ["get_churn_replay", { run_id: run.id }],
    ] as const;
    let successful = 0;
    for (const [name, extra] of cases) {
      for (const id of [full, metadata]) {
        const body = await mcpPayload(
          await app.request("/mcp", mcpRequest(pat.token, name, { ...extra, client_id: id })),
        );
        expect(body.result?.isError).not.toBe(true);
        expect(body.result?.structuredContent).toBeDefined();
        successful++;
        if (id === metadata)
          expect(JSON.stringify(body)).not.toMatch(/untrusted_quote|Ignore previous/);
        if (name === "search_evidence" && id === full) {
          expect(mcpEvidence.parse(body.result?.structuredContent).items[0]?.untrusted_quote).toBe(
            INJECTION,
          );
          expect(body.result?.content?.[0]?.text).toContain(
            `<untrusted_quote>${INJECTION}</untrusted_quote>`,
          );
        }
      }
      for (const client_id of [foreign, excluded, randomUUID()]) {
        const body = await mcpPayload(
          await app.request("/mcp", mcpRequest(pat.token, name, { ...extra, client_id })),
        );
        expect(body.result).toEqual({
          isError: true,
          content: [{ type: "text", text: "Client not found." }],
        });
      }
    }
    for (const name of [
      "get_clients_needing_attention",
      "list_clients",
      "get_decisions",
      "get_churn_replay",
    ]) {
      const body = await mcpPayload(await app.request("/mcp", mcpRequest(pat.token, name)));
      expect(body.result?.isError).not.toBe(true);
      successful++;
      expect(JSON.stringify(body)).not.toMatch(/Synthetic Excluded|Synthetic Foreign/);
      const forged = await mcpPayload(
        await app.request("/mcp", mcpRequest(pat.token, name, { workspace_id: foreignWs })),
      );
      expect(forged.error ?? forged.result?.isError).toBeTruthy();
    }
    const audit = checked(
      await admin
        .from("audit_log")
        .select("actor_type, actor_id, action, target_type, target_id, metadata")
        .eq("workspace_id", ws)
        .like("action", "mcp.%"),
    );
    expect(audit).toHaveLength(successful);
    expect(audit?.filter((a) => a.action === "mcp.get_decisions")).toHaveLength(1);
    for (const row of audit ?? []) {
      expect(row.actor_type).toBe("mcp");
      expect(row.actor_id).toBe(pat.id);
      expect(Object.keys(row.metadata as object)).toEqual(["count"]);
      if (row.target_id) expect([full, metadata]).toContain(row.target_id);
    }
  } finally {
    await admin.from("workspace").delete().in("id", workspaces);
    for (const id of users) await admin.auth.admin.deleteUser(id);
  }
}, 60000);

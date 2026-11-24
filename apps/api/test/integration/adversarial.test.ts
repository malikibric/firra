/** T14 caller-RLS proof on local Supabase: isolation, roles, policy, every surface, MCP and audit. */
import { randomUUID } from "node:crypto";
import { apiError } from "@firra/contracts";
import type { SupabaseClient } from "@supabase/supabase-js";
import { describe, expect, it } from "vitest";
import { SupabaseReadStore } from "../../src/lib/read-store.js";
import type { ReplayDatabase } from "../../src/lib/replay-schema.js";
import { SupabaseRepository } from "../../src/lib/repository.js";
import { SupabaseMcpTokenAdmin } from "../../src/mcp/admin.js";
import { SupabaseMcpTokenStore, SupabaseSessionMinter } from "../../src/mcp/tokens.js";
import { testApp } from "../helpers.js";
import { NARRATIVE } from "../replay-fixtures.js";
import { INJECTION, mcpPayload, mcpRequest } from "../mcp-tool-helpers.js";
import {
  admin,
  appFor,
  PUBLISHABLE_KEY,
  seedEvidenceSql,
  sqlFixture,
  SUPABASE_URL,
  userWithToken,
  workspace,
} from "./read-helpers.js";

const EMAIL_KEYS = ["quote", "untrusted_quote", "body_clean", "raw_object_uri", "deep_link"];
const SECRET_KEYS = [
  "token_ciphertext",
  "token_hash",
  "wrapped_dek",
  "service_role",
  "body_clean",
  "raw_object_uri",
  "password",
];

function findKeys(value: unknown, keys: readonly string[]): string[] {
  const found: string[] = [];
  const walk = (node: unknown, path: string): void => {
    if (Array.isArray(node))
      node.forEach((child, index) => {
        walk(child, `${path}[${index}]`);
      });
    else if (node && typeof node === "object")
      for (const [key, child] of Object.entries(node)) {
        if (keys.includes(key)) found.push(`${path}.${key}`);
        walk(child, `${path}.${key}`);
      }
  };
  walk(value, "$");
  return found;
}

function checked<T>(result: { data: T; error: unknown }): T {
  if (result.error)
    throw new Error(`synthetic integration fixture failed: ${JSON.stringify(result.error)}`);
  return result.data;
}

async function client(
  workspaceId: string,
  name: string,
  policy: "full" | "metadata_only" | "excluded",
): Promise<string> {
  const row = checked(
    await admin
      .from("client")
      .insert({ workspace_id: workspaceId, name, policy })
      .select("id")
      .single(),
  );
  if (!row) throw new Error("fixture client missing");
  return row.id;
}

async function assessment(workspaceId: string, clientId: string, metadata: boolean): Promise<void> {
  const today = new Date().toISOString().slice(0, 10);
  checked(
    await admin.from("account_assessment").insert({
      workspace_id: workspaceId,
      client_id: clientId,
      as_of: today,
      run_kind: "live",
      assessment_version: "adversarial",
      rule_version: "adversarial",
      risk_state: "high",
      previous_risk_state: "watch",
      state_since: today,
      expansion_state: "none",
      strength: "moderate",
      play: "fix_first",
      caveats: [{ code: "low_data", detail: "Few client messages." }],
      drivers: [
        {
          type: metadata ? "renewal_window" : "delivery_complaint",
          family: metadata ? "commercial" : "delivery",
          severity: "high",
          basis: metadata ? "metadata" : "evidence",
          occurred_at: `${today}T08:00:00+00:00`,
          primary: true,
          evidence_ids: [],
          inputs: metadata ? { days_to_notice: 10 } : {},
        },
      ],
    }),
  );
}

/** A metadata-only brief for one client, with the evidence chain a calendar event needs. */
function seedMeetingBrief(ws: string, clientId: string): void {
  const content =
    '{"since_last_meeting":{"records":0,"last_held_at":null},"open_commitments":[],' +
    '"unresolved_complaints":[],"stakeholder_changes":[],"renewal":null,"unknowns":[],"questions":[]}';
  sqlFixture(`
    do $$ declare c uuid; sr uuid; ce uuid; begin
      insert into public.connection (workspace_id, provider, mode, account_label)
        values ('${ws}', 'email_upload', 'upload', 'adv-meet-${randomUUID()}') returning id into c;
      insert into public.source_record (workspace_id, connection_id, provider, external_id, kind,
          client_id, policy_at_ingest, occurred_at, direction, word_count, body_clean)
        values ('${ws}', c, 'email_upload', 'adv-meet-${randomUUID()}', 'email', '${clientId}',
          'metadata_only', now(), 'in', 0, null) returning id into sr;
      insert into public.calendar_event (workspace_id, source_record_id, client_id, starts_at,
          duration_min, status, attendee_count)
        values ('${ws}', sr, '${clientId}', now() + interval '2 hours', 30, 'scheduled', 2)
        returning id into ce;
      insert into public.meeting_brief (workspace_id, calendar_event_id, client_id, starts_at,
          locale, basis, content, version)
        values ('${ws}', ce, '${clientId}', now() + interval '2 hours', 'en', 'metadata',
          '${content}'::jsonb, 'facts.v1');
    end $$;`);
}

async function seedReplay(ws: string, clientId: string): Promise<string> {
  const run = checked(
    await admin
      .from("replay_run")
      .insert({
        workspace_id: ws,
        window_start: "2026-09-01",
        window_end: "2026-09-30",
        step_days: 7,
        status: "succeeded",
        finished_at: "2026-09-30T12:00:00Z",
      })
      .select("id")
      .single(),
  );
  if (!run) throw new Error("fixture replay missing");
  const replayDb = admin as unknown as SupabaseClient<ReplayDatabase>;
  checked(
    await replayDb.from("replay_narrative").insert({
      workspace_id: ws,
      replay_run_id: run.id,
      client_id: clientId,
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
  return run.id;
}

describe("adversarial caller RLS (local Supabase)", () => {
  it("isolates workspaces, applies policy on every surface, refuses non-owners and audits each read once", async () => {
    const workspaces: string[] = [];
    const userIds: string[] = [];
    try {
      const ws = await workspace();
      workspaces.push(ws);
      const foreignWs = await workspace();
      workspaces.push(foreignWs);
      const owner = await userWithToken("adv-owner");
      userIds.push(owner.id);
      const administrator = await userWithToken("adv-admin");
      userIds.push(administrator.id);
      const contributor = await userWithToken("adv-contributor");
      userIds.push(contributor.id);
      const outsider = await userWithToken("adv-outsider");
      userIds.push(outsider.id);
      const ownerB = await userWithToken("adv-owner-b");
      userIds.push(ownerB.id);
      checked(
        await admin.from("membership").insert([
          { workspace_id: ws, user_id: owner.id, role: "owner" },
          { workspace_id: ws, user_id: administrator.id, role: "admin" },
          { workspace_id: foreignWs, user_id: ownerB.id, role: "owner" },
        ]),
      );
      checked(
        await admin
          .from("contributor")
          .insert({ workspace_id: ws, email: contributor.email, status: "active" }),
      );
      const full = await client(ws, "Adv Full", "full");
      const metadata = await client(ws, "Adv Metadata", "metadata_only");
      const excluded = await client(ws, "Adv Excluded", "excluded");
      const foreign = await client(foreignWs, "Adv Foreign", "full");
      const allClients = [
        [ws, full, false],
        [ws, metadata, true],
        [ws, excluded, false],
        [foreignWs, foreign, false],
      ] as const;
      for (const [workspaceId, clientId, isMetadata] of allClients)
        await assessment(workspaceId, clientId, isMetadata);
      for (const [workspaceId, clientId] of allClients)
        checked(
          await admin.from("decision_card").insert({
            workspace_id: workspaceId,
            client_id: clientId,
            week: "2026-09-28",
            kind: "risk",
            title: "Adversarial card",
            body: "Adversarial metadata wording",
            locale: "en",
            score: 1,
            priority: 1,
            status: "new",
            inputs: {},
            model: "fixture",
            prompt_version: "fixture",
            dedupe_key: `adv-${clientId}`,
            play: "fix_first",
            facts: [{ basis: "metadata" }],
            interpretation: "Synthetic",
            recommendation: "Synthetic",
            strength: "moderate",
          }),
        );
      sqlFixture(
        `do $$ declare c uuid; r uuid; e uuid; begin ${seedEvidenceSql(ws, full)} end $$;`,
      );
      const evidence = checked(
        await admin.from("evidence").select("id, quote").eq("client_id", full).single(),
      );
      if (!evidence) throw new Error("fixture evidence missing");
      const runId = await seedReplay(ws, metadata);
      seedMeetingBrief(ws, metadata);

      const app = appFor();
      const base = `/v1/workspaces/${ws}`;
      const ownerHeaders = { Authorization: `Bearer ${owner.token}` };
      // Requirement 10: count the owner's successful reads, then compare to the audit rows.
      let ownerReads = 0;
      const ownerRead = async (url: string): Promise<Response> => {
        const res = await app.request(url, { headers: ownerHeaders });
        expect(res.status, url).toBe(200);
        ownerReads += 1;
        return res;
      };
      const ownerGet = (url: string, headers = ownerHeaders) => app.request(url, { headers });

      // Lists: full + metadata only; never excluded or foreign, on every list surface.
      for (const path of ["/attention", "/changes", "/clients", "/decisions"]) {
        const res = await ownerRead(`${base}${path}`);
        const body = await res.text();
        expect(body, path).toContain("Adv Metadata");
        expect(body, path).not.toContain("Adv Excluded");
        expect(body, path).not.toContain("Adv Foreign");
        expect(findKeys(JSON.parse(body), SECRET_KEYS), path).toEqual([]);
      }
      const attentionText = await (await ownerRead(`${base}/attention`)).text();
      const metadataCard = (
        (await (await ownerRead(`${base}/decisions`)).json()) as {
          items: { client_id: string; evidence_ids: string[] }[];
        }
      ).items.find((item) => item.client_id === metadata);
      expect(metadataCard?.evidence_ids).toEqual([]);

      // A foreign client id is exactly a missing one, body included.
      const foreignRead = apiError.parse(
        await (await ownerGet(`${base}/clients/${foreign}`)).json(),
      );
      const missingRead = apiError.parse(
        await (await ownerGet(`${base}/clients/${randomUUID()}`)).json(),
      );
      expect(foreignRead.error).toBe("not_found");
      expect(foreignRead.message).toBe(missingRead.message);
      expect((await ownerGet(`${base}/clients/${excluded}`)).status).toBe(404);

      // Detail, timeline and evidence follow the same policy.
      expect((await ownerRead(`${base}/clients/${full}`)).status).toBe(200);
      expect(
        findKeys(await (await ownerRead(`${base}/clients/${metadata}`)).json(), EMAIL_KEYS),
      ).toEqual([]);
      expect(
        findKeys(
          await (await ownerRead(`${base}/clients/${metadata}/timeline`)).json(),
          EMAIL_KEYS,
        ),
      ).toEqual([]);
      for (const [clientId, suffix] of [
        [excluded, ""],
        [foreign, ""],
        [excluded, "/timeline"],
        [foreign, "/timeline"],
      ] as const)
        expect((await ownerGet(`${base}/clients/${clientId}${suffix}`)).status, suffix).toBe(404);
      expect((await ownerRead(`${base}/clients/${metadata}/evidence`)).status).toBe(200);
      expect(
        (
          (await (await ownerRead(`${base}/clients/${metadata}/evidence`)).json()) as {
            items: unknown[];
          }
        ).items,
      ).toEqual([]);
      const fullEvidence = await (await ownerRead(`${base}/clients/${full}/evidence`)).json();
      expect(findKeys(fullEvidence, EMAIL_KEYS)).toContain("$.items[0].quote");
      expect(
        findKeys(await (await ownerRead(`${base}/evidence/${evidence.id}`)).json(), EMAIL_KEYS),
      ).toContain("$.quote");

      // Meetings and replay: metadata only, metadata-only text, excluded/foreign absent.
      const meetings = await (await ownerRead(`${base}/meetings/upcoming`)).text();
      expect(meetings).toContain("Adv Metadata");
      expect(meetings).not.toContain("Adv Excluded");
      expect(meetings).not.toContain("Adv Foreign");
      expect(findKeys(JSON.parse(meetings), EMAIL_KEYS)).toEqual([]);
      expect((await ownerRead(`${base}/replay/runs`)).status).toBe(200);
      expect(
        findKeys(
          await (await ownerRead(`${base}/replay/runs/${runId}/clients/${metadata}`)).json(),
          EMAIL_KEYS,
        ),
      ).toEqual([]);
      for (const clientId of [excluded, foreign])
        expect((await ownerGet(`${base}/replay/runs/${runId}/clients/${clientId}`)).status).toBe(
          404,
        );

      // Roles: contributor and outsider are refused; admin equals owner; a foreign owner is refused.
      for (const token of [contributor.token, outsider.token, ownerB.token]) {
        const res = await app.request(`${base}/attention`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        expect(res.status, token).toBe(403);
      }
      const adminAttention = await app.request(`${base}/attention`, {
        headers: { Authorization: `Bearer ${administrator.token}` },
      });
      expect(adminAttention.status).toBe(200);
      expect(((await adminAttention.json()) as { items: unknown[] }).items).toEqual(
        (JSON.parse(attentionText) as { items: unknown[] }).items,
      );

      // Mutations on an excluded or foreign target are indistinguishable 404s.
      const jsonHeaders = { ...ownerHeaders, "Content-Type": "application/json" };
      for (const clientId of [excluded, foreign, randomUUID()])
        expect(
          (
            await app.request(`${base}/clients/${clientId}/outcomes`, {
              method: "POST",
              headers: jsonHeaders,
              body: JSON.stringify({ event: "churned", occurred_at: "2026-10-01" }),
            })
          ).status,
        ).toBe(404);
      expect(
        (
          await app.request(`${base}/clients`, {
            method: "POST",
            headers: {
              Authorization: `Bearer ${contributor.token}`,
              "Content-Type": "application/json",
            },
            body: JSON.stringify({ name: "Nope", policy: "full", domains: [] }),
          })
        ).status,
      ).toBe(403);

      // The real policy change: full -> excluded, then re-read every surface.
      const policyChange = await app.request(`${base}/clients/${full}/policy`, {
        method: "PUT",
        headers: jsonHeaders,
        body: JSON.stringify({ policy: "excluded", confirm_exclusion: true }),
      });
      expect(policyChange.status).toBe(204);
      for (const path of [
        "/attention",
        "/changes",
        "/clients",
        "/decisions",
        "/meetings/upcoming",
      ]) {
        const text = await (await ownerRead(`${base}${path}`)).text();
        expect(text, path).not.toContain("Adv Full");
      }
      expect((await ownerGet(`${base}/clients/${full}`)).status).toBe(404);
      expect((await ownerGet(`${base}/clients/${full}/timeline`)).status).toBe(404);
      expect((await ownerGet(`${base}/clients/${full}/evidence`)).status).toBe(404);
      expect((await ownerGet(`${base}/replay/runs/${runId}/clients/${full}`)).status).toBe(404);

      // Requirement 10: exactly one audit row per successful owner read, none for refused calls.
      const audit = checked(
        await admin
          .from("audit_log")
          .select("action")
          .eq("workspace_id", ws)
          .eq("actor_id", owner.id)
          .like("action", "read.%"),
      );
      expect(audit).toHaveLength(ownerReads);
      const refusals = checked(
        await admin
          .from("audit_log")
          .select("id")
          .eq("workspace_id", ws)
          .eq("actor_id", contributor.id),
      );
      expect(refusals).toEqual([]);
    } finally {
      await admin.from("workspace").delete().in("id", workspaces);
      for (const id of userIds) await admin.auth.admin.deleteUser(id);
    }
  }, 90000);

  it("runs the real MCP store: one audit row per call, a revoked token refused, metadata quote hidden", async () => {
    const workspaces: string[] = [];
    const userIds: string[] = [];
    try {
      const ws = await workspace();
      workspaces.push(ws);
      const owner = await userWithToken("adv-mcp-owner");
      userIds.push(owner.id);
      checked(
        await admin
          .from("membership")
          .insert({ workspace_id: ws, user_id: owner.id, role: "owner" }),
      );
      const full = await client(ws, "Adv MCP Full", "full");
      const metadata = await client(ws, "Adv MCP Metadata", "metadata_only");
      await assessment(ws, full, false);
      await assessment(ws, metadata, true);
      sqlFixture(
        `do $$ declare c uuid; r uuid; e uuid; begin ${seedEvidenceSql(ws, full)} end $$;`,
      );

      const tokenAdmin = new SupabaseMcpTokenAdmin(SUPABASE_URL, PUBLISHABLE_KEY);
      const pat = await tokenAdmin.create({ userId: owner.id, token: owner.token }, ws, {
        name: "adversarial",
        days: 1,
      });
      const app = testApp({
        repo: new SupabaseRepository(admin),
        readStore: new SupabaseReadStore(SUPABASE_URL, PUBLISHABLE_KEY),
        mcpTokenStore: new SupabaseMcpTokenStore(admin),
        sessionMinter: new SupabaseSessionMinter(admin, SUPABASE_URL, PUBLISHABLE_KEY),
      }).app;
      const calls = [
        ["list_clients", {}],
        ["get_clients_needing_attention", {}],
      ] as const;
      for (const [name, args] of calls) {
        const payload = await mcpPayload(
          await app.request("/mcp", mcpRequest(pat.token, name, args)),
        );
        expect(payload.result?.isError, name).not.toBe(true);
      }
      const metadataEvidence = await mcpPayload(
        await app.request(
          "/mcp",
          mcpRequest(pat.token, "search_evidence", { client_id: metadata }),
        ),
      );
      expect(metadataEvidence.result?.isError).not.toBe(true);
      expect(JSON.stringify(metadataEvidence)).not.toContain("untrusted_quote");
      expect(JSON.stringify(metadataEvidence)).not.toContain(INJECTION);

      const audit = checked(
        await admin
          .from("audit_log")
          .select("action")
          .eq("workspace_id", ws)
          .eq("actor_type", "mcp")
          .like("action", "mcp.%"),
      );
      expect(audit?.map((row) => row.action).sort()).toEqual(
        ["mcp.get_clients_needing_attention", "mcp.list_clients", "mcp.search_evidence"].sort(),
      );

      await tokenAdmin.revoke({ userId: owner.id, token: owner.token }, ws, pat.id);
      const refused = await app.request("/mcp", mcpRequest(pat.token, "list_clients", {}));
      expect(refused.status).toBe(401);
      const after = checked(
        await admin
          .from("audit_log")
          .select("action")
          .eq("workspace_id", ws)
          .eq("actor_type", "mcp")
          .like("action", "mcp.%"),
      );
      expect(after).toHaveLength(3);
    } finally {
      await admin.from("workspace").delete().in("id", workspaces);
      for (const id of userIds) await admin.auth.admin.deleteUser(id);
    }
  }, 90000);
});

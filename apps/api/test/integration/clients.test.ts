import { required } from "../client-fixtures.js";
/** Client portfolio/detail against real RLS and policy-derived deletion in local Supabase. */
import { randomUUID } from "node:crypto";
import { apiError, clientsResponse, clientDetailResponse, evidenceItem } from "@firra/contracts";
import type { Database } from "@firra/db-types";
import { createClient } from "@supabase/supabase-js";
import { describe, expect, it } from "vitest";
import {
  admin,
  appFor,
  PUBLISHABLE_KEY,
  SUPABASE_URL,
  seedEvidenceSql,
  sqlFixture,
  userWithToken,
  workspace,
} from "./read-helpers.js";
function checked<T>(result: { data: T; error: unknown }): T {
  if (result.error) throw new Error("synthetic integration fixture failed");
  return result.data;
}
async function client(
  workspaceId: string,
  name: string,
  status: "active" | "churned" | "prospect" = "active",
) {
  return required(
    checked(
      await admin
        .from("client")
        .insert({ workspace_id: workspaceId, name, status, policy: "full" })
        .select("id")
        .single(),
    ),
  ).id;
}
async function assessment(
  workspaceId: string,
  clientId: string,
  asOf: string,
  metadata = false,
  previous: "stable" | "watch" = "stable",
) {
  checked(
    await admin.from("account_assessment").insert({
      workspace_id: workspaceId,
      client_id: clientId,
      as_of: asOf,
      run_kind: "live",
      assessment_version: "test",
      rule_version: "test",
      risk_state: "watch",
      previous_risk_state: previous,
      state_since: asOf,
      expansion_state: "none",
      strength: "moderate",
      play: "monitor",
      caveats: [
        { code: "low_data", detail: "Few client messages.", private_extra: "PRIVATE BODY" },
      ],
      drivers: [
        {
          type: metadata ? "renewal_window" : "delivery_complaint",
          family: metadata ? "commercial" : "delivery",
          severity: "medium",
          basis: metadata ? "metadata" : "evidence",
          occurred_at: `${asOf}T00:00:00+00:00`,
          primary: true,
          evidence_ids: [],
          inputs: metadata
            ? { days_to_notice: 10, quote: "PRIVATE BODY" }
            : { quote: "PRIVATE BODY" },
        },
      ],
    }),
  );
}
describe("Client reads (local Supabase)", () => {
  it("isolates clients, checks both routes for owner/admin versus contributors, and proves metadata deletion", async () => {
    const workspaces: string[] = [];
    const users: {
      id: string;
      token: string;
    }[] = [];
    try {
      const ws = await workspace();
      workspaces.push(ws);
      const foreign = await workspace();
      workspaces.push(foreign);
      const owner = await userWithToken("clients-owner");
      users.push(owner);
      const administrator = await userWithToken("clients-admin");
      users.push(administrator);
      const contributor = await userWithToken("clients-contributor");
      users.push(contributor);
      const outsider = await userWithToken("clients-outsider");
      users.push(outsider);
      checked(
        await admin.from("membership").insert([
          { workspace_id: ws, user_id: owner.id, role: "owner" },
          { workspace_id: ws, user_id: administrator.id, role: "admin" },
          { workspace_id: foreign, user_id: outsider.id, role: "owner" },
        ]),
      );
      checked(
        await admin
          .from("contributor")
          .insert({ workspace_id: ws, email: contributor.email, status: "active" }),
      );
      const full = await client(ws, "A Full");
      const meta = await client(ws, "B Metadata");
      const excluded = await client(ws, "Excluded");
      const churned = await client(ws, "C Churned", "churned");
      const prospect = await client(ws, "D Prospect", "prospect");
      const foreignClient = await client(foreign, "Foreign");
      const oldDate = "2025-01-01";
      await assessment(ws, full, oldDate);
      await assessment(ws, full, "2025-01-02", false, "watch");
      const replay = required(
        checked(
          await admin
            .from("replay_run")
            .insert({ workspace_id: ws, window_start: oldDate, window_end: "2025-01-02" })
            .select("id")
            .single(),
        ),
      );
      checked(
        await admin.from("account_assessment").insert({
          workspace_id: ws,
          client_id: full,
          as_of: "2025-01-03",
          run_kind: "replay",
          replay_run_id: replay.id,
          assessment_version: "test",
          rule_version: "test",
          risk_state: "high",
          state_since: "2025-01-03",
          expansion_state: "none",
          strength: "strong",
        }),
      );
      await assessment(ws, meta, oldDate);
      const person = required(
        checked(
          await admin
            .from("person")
            .insert({
              workspace_id: ws,
              client_id: meta,
              side: "client",
              display_name: "Synthetic client stakeholder",
              role_title: "Body-derived title",
              is_decision_maker: true,
              last_seen_at: `${oldDate}T00:00:00+00:00`,
            })
            .select("id")
            .single(),
        ),
      );
      checked(
        await admin.from("person_observation").insert({
          workspace_id: ws,
          client_id: meta,
          person_id: person.id,
          observed_at: `${oldDate}T00:00:00+00:00`,
          source: "signature",
          role_title: "Body-derived title",
          is_decision_maker: true,
        }),
      );
      for (const clientId of [full, meta]) {
        sqlFixture(`
          do $$ declare c uuid; r uuid; e uuid; p uuid; begin
            ${seedEvidenceSql(ws, clientId)}
            insert into public.commitment (workspace_id, client_id, owner_side, text, status, stated_at, dedupe_key)
              values ('${ws}', '${clientId}', 'firm', 'Synthetic commitment', 'open', '${oldDate}T00:00:00+00:00', '${randomUUID()}')
              returning id into p;
            insert into public.commitment_evidence (workspace_id, commitment_id, evidence_id, client_id)
              values ('${ws}', p, e, '${clientId}');
            insert into public.client_goal (workspace_id, client_id, text, stated_at, dedupe_key)
              values ('${ws}', '${clientId}', 'Synthetic goal', '${oldDate}T00:00:00+00:00', '${randomUUID()}')
              returning id into p;
            insert into public.client_goal_evidence (workspace_id, client_goal_id, evidence_id, client_id)
              values ('${ws}', p, e, '${clientId}');
            insert into public.latent_need (workspace_id, client_id, topic, mention_count, thread_count,
                first_seen_at, last_seen_at, cluster_key, rule_version, status)
              values ('${ws}', '${clientId}', 'Synthetic need', 3, 2, '${oldDate}T00:00:00+00:00',
                '${oldDate}T00:00:00+00:00', '${randomUUID()}', 'test', 'open')
              returning id into p;
            insert into public.latent_need_evidence (workspace_id, latent_need_id, evidence_id, client_id)
              values ('${ws}', p, e, '${clientId}');
          end $$;`);
        checked(
          await admin.from("outcome").insert({
            workspace_id: ws,
            client_id: clientId,
            event: "renewed",
            occurred_at: oldDate,
          }),
        );
      }
      checked(
        await admin.from("person").insert({
          workspace_id: ws,
          client_id: full,
          side: "client",
          display_name: "Synthetic champion",
          is_champion: true,
        }),
      );
      const unknowns = [{ code: "budget_unknown", detail: "Budget not stated." }];
      checked(
        await admin.from("client_card").insert([
          {
            workspace_id: ws,
            client_id: full,
            version: 1,
            content: { unknowns: [{ code: "old", detail: "Old checklist." }] },
          },
          {
            workspace_id: ws,
            client_id: full,
            version: 2,
            content: { unknowns, private_extra: "PRIVATE BODY" },
          },
          {
            workspace_id: foreign,
            client_id: foreignClient,
            version: 1,
            content: { unknowns: [{ code: "foreign", detail: "Foreign checklist." }] },
          },
        ]),
      );
      const callerDb = createClient<Database>(SUPABASE_URL, PUBLISHABLE_KEY, {
        auth: { persistSession: false },
        global: { headers: { Authorization: `Bearer ${owner.token}` } },
      });
      checked(
        await callerDb.rpc("set_client_policy", { p_client_id: excluded, p_policy: "excluded" }),
      );
      checked(
        await callerDb.rpc("set_client_policy", { p_client_id: meta, p_policy: "metadata_only" }),
      );
      for (const table of [
        "commitment",
        "client_goal",
        "latent_need",
        "account_assessment",
        "person_observation",
      ] as const) {
        const remaining = checked(
          await admin.from(table).select("id").eq("workspace_id", ws).eq("client_id", meta),
        );
        expect(remaining).toEqual([]);
      }
      const metadataUnknowns = [{ code: "budget_unknown", detail: "budget_unknown" }];
      checked(
        await admin.from("client_card").insert({
          workspace_id: ws,
          client_id: meta,
          version: 1,
          content: { unknowns: metadataUnknowns },
        }),
      );
      await assessment(ws, meta, "2025-01-02", true);
      const app = appFor();
      const list = `/v1/workspaces/${ws}/clients`;
      const ownerHeaders = { Authorization: `Bearer ${owner.token}` };
      const response = await app.request(list, { headers: ownerHeaders });
      expect(response.status).toBe(200);
      const portfolio = clientsResponse.parse(await response.json());
      expect(portfolio.items.map((c) => c.id)).toEqual([full, meta]);
      expect(required(required(portfolio.items[0]).assessment).as_of).toBe("2025-01-02");
      const noAssessment = await app.request(list + "?status=churned", { headers: ownerHeaders });
      expect(noAssessment.status).toBe(200);
      expect(clientsResponse.parse(await noAssessment.json()).items).toMatchObject([
        { id: churned, assessment: null },
      ]);
      const all = await app.request(list + "?status=all", { headers: ownerHeaders });
      expect(all.status).toBe(200);
      expect(clientsResponse.parse(await all.json()).items.map((c) => c.id)).toEqual([
        full,
        meta,
        churned,
        prospect,
      ]);
      const fullResponse = await app.request(`${list}/${full}`, { headers: ownerHeaders });
      expect(fullResponse.status).toBe(200);
      const detail = clientDetailResponse.parse(await fullResponse.json()).client;
      expect(required(detail.assessment).risk_state).toBe("watch");
      expect(detail.commitments).toHaveLength(1);
      expect(detail.goals).toHaveLength(1);
      expect(detail.needs).toHaveLength(1);
      expect(detail.outcomes).toHaveLength(1);
      expect(detail.state_history.map((h) => h.as_of)).toEqual([oldDate]);
      expect(detail.stakeholders[0]?.is_champion).toBe(true);
      expect(detail.unknowns).toEqual(unknowns);
      for (const claims of [detail.commitments, detail.goals, detail.needs]) {
        const ids = required(required(claims[0]).evidence_ids);
        expect(ids).toHaveLength(1);
        const quote = await app.request(`/v1/workspaces/${ws}/evidence/${required(ids[0])}`, {
          headers: ownerHeaders,
        });
        expect(quote.status).toBe(200);
        expect(evidenceItem.parse(await quote.json()).id).toBe(ids[0]);
      }
      const metaResponse = await app.request(`${list}/${meta}`, { headers: ownerHeaders });
      expect(metaResponse.status).toBe(200);
      const metadata = clientDetailResponse.parse(await metaResponse.json()).client;
      expect(required(metadata.assessment).drivers[0]).toMatchObject({
        basis: "metadata",
        inputs: { days_to_notice: 10 },
        evidence_ids: [],
      });
      expect(metadata.unknowns).toEqual(metadataUnknowns);
      expect(
        [...metadata.commitments, ...metadata.goals, ...metadata.needs].flatMap(
          (claim) => claim.evidence_ids ?? [],
        ),
      ).toEqual([]);
      expect(metadata.commitments).toEqual([]);
      expect(metadata.goals).toEqual([]);
      expect(metadata.needs).toEqual([]);
      expect(metadata.stakeholders).toMatchObject([
        {
          display_name: "Synthetic client stakeholder",
          role_title: null,
          is_decision_maker: false,
        },
      ]);
      expect(metadata.outcomes).toHaveLength(1);
      expect(JSON.stringify(metadata)).not.toContain("PRIVATE BODY");
      expect(JSON.stringify(metadata)).not.toContain("Body-derived title");
      for (const clientId of [excluded, foreignClient, randomUUID()]) {
        const res = await app.request(`${list}/${clientId}`, { headers: ownerHeaders });
        expect(res.status).toBe(404);
        expect(apiError.parse(await res.json()).error).toBe("not_found");
      }
      for (const user of [contributor, outsider]) {
        for (const url of [list, `${list}/${full}`]) {
          const res = await app.request(url, {
            headers: { Authorization: `Bearer ${user.token}` },
          });
          expect(res.status).toBe(403);
          apiError.parse(await res.json());
        }
      }
      for (const [url, schema] of [
        [list, clientsResponse],
        [`${list}/${full}`, clientDetailResponse],
      ] as const) {
        const res = await app.request(url, {
          headers: { Authorization: `Bearer ${administrator.token}` },
        });
        expect(res.status).toBe(200);
        schema.parse(await res.json());
      }
      const audits = checked(
        await admin
          .from("audit_log")
          .select("action, actor_id, target_type, target_id, metadata")
          .eq("workspace_id", ws)
          .in("action", ["read.clients", "read.client_detail"]),
      );
      expect(audits).toHaveLength(7);
      expect(required(audits).filter((a) => a.action === "read.clients")).toHaveLength(4);
      expect(required(audits).filter((a) => a.action === "read.client_detail")).toHaveLength(3);
      for (const audit of required(audits)) {
        expect([owner.id, administrator.id]).toContain(audit.actor_id);
        if (audit.action === "read.client_detail") {
          expect(audit.target_type).toBe("client");
          expect([full, meta]).toContain(audit.target_id);
        } else {
          expect(audit.target_type).toBeNull();
          expect(audit.target_id).toBeNull();
        }
      }
    } finally {
      for (const ws of workspaces.reverse())
        checked(await admin.from("workspace").delete().eq("id", ws));
      for (const user of users.reverse()) {
        checked<unknown>(await admin.auth.admin.deleteUser(user.id));
      }
    }
  });
});

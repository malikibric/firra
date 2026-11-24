/** Synthetic local Supabase coverage: real caller RLS and real policy downgrade deletion. */
import { randomUUID } from "node:crypto";
import { apiError, evidenceItem, evidenceResponse, timelineResponse } from "@firra/contracts";
import type { Database } from "@firra/db-types";
import { createClient } from "@supabase/supabase-js";
import { describe, expect, it } from "vitest";
import { required } from "../client-fixtures.js";
import {
  admin,
  appFor,
  userWithToken,
  workspace,
  SUPABASE_URL,
  PUBLISHABLE_KEY,
  sqlFixture,
} from "./read-helpers.js";
function checked<T>(result: { data: T; error: unknown }): T {
  if (result.error) throw new Error("synthetic timeline fixture failed");
  return result.data;
}
describe("Timeline/evidence reads (local Supabase)", () => {
  it("paginates across inserts, isolates tenants and roles, and deletes body-derived timeline/evidence on downgrade", async () => {
    const workspaces: string[] = [];
    const users: { id: string }[] = [];
    try {
      const ws = await workspace();
      workspaces.push(ws);
      const other = await workspace();
      workspaces.push(other);
      const owner = await userWithToken("timeline-owner");
      users.push(owner);
      const administrator = await userWithToken("timeline-admin");
      users.push(administrator);
      const outsider = await userWithToken("timeline-outsider");
      users.push(outsider);
      const contributor = await userWithToken("timeline-contributor");
      users.push(contributor);
      checked(
        await admin.from("membership").insert([
          { workspace_id: ws, user_id: owner.id, role: "owner" },
          { workspace_id: ws, user_id: administrator.id, role: "admin" },
          { workspace_id: other, user_id: outsider.id, role: "owner" },
        ]),
      );
      checked(
        await admin.from("contributor").insert({ workspace_id: ws, email: contributor.email }),
      );
      const makeClient = async (workspaceId: string) =>
        required(
          checked(
            await admin
              .from("client")
              .insert({ workspace_id: workspaceId, name: "Synthetic client", policy: "full" })
              .select("id")
              .single(),
          ),
        ).id;
      const client = await makeClient(ws);
      const foreign = await makeClient(other);
      const excluded = await makeClient(ws);
      const connection = required(
        checked(
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
        ),
      ).id;
      const person = required(
        checked(
          await admin
            .from("person")
            .insert({
              workspace_id: ws,
              client_id: client,
              side: "client",
              display_name: "Synthetic stakeholder",
              role_title: "CEO",
            })
            .select("id")
            .single(),
        ),
      ).id;
      const seedEvidence = async (date: string, clientId = client) => {
        const record = required(
          checked(
            await admin
              .from("source_record")
              .insert({
                workspace_id: ws,
                client_id: clientId,
                connection_id: connection,
                provider: "email_upload",
                kind: "email",
                direction: "in",
                policy_at_ingest: "full",
                external_id: randomUUID(),
                occurred_at: date,
                author_person_id: person,
                body_clean: "Synthetic verified evidence quote",
                metadata: { subject: "  " + "S".repeat(140) + "  " },
              })
              .select("id")
              .single(),
          ),
        ).id;
        return required(
          checked(
            await admin
              .from("evidence")
              .insert({
                workspace_id: ws,
                client_id: clientId,
                source_record_id: record,
                quote: "Synthetic verified evidence quote",
                locator: { start: 0, end: 33 },
              })
              .select("id")
              .single(),
          ),
        ).id;
      };
      const evidenceIds = [
        await seedEvidence("2025-01-02T00:00:00Z"),
        await seedEvidence("2025-01-02T00:00:00Z"),
        await seedEvidence("2025-01-01T00:00:00Z"),
      ];
      const hiddenEvidence = await seedEvidence("2025-01-01T00:00:00Z", excluded);
      sqlFixture(`
        do $$ declare p uuid; begin
          insert into public.commitment (workspace_id, client_id, owner_side, text, dedupe_key, stated_at,
              due_date, status, closed_at)
            values ('${ws}', '${client}', 'firm', 'Synthetic commitment', '${randomUUID()}',
              '2025-01-01T00:00:00Z', '2025-01-02', 'done', '2025-01-03T00:00:00Z')
            returning id into p;
          insert into public.commitment_evidence (workspace_id, commitment_id, evidence_id, client_id)
            values ('${ws}', p, '${evidenceIds[0]}', '${client}');
        end $$;`);
      const seedAssessment = async (date: string) =>
        checked(
          await admin.from("account_assessment").insert({
            workspace_id: ws,
            client_id: client,
            as_of: date,
            run_kind: "live",
            assessment_version: "test",
            rule_version: "test",
            risk_state: "watch",
            previous_risk_state: "stable",
            state_since: date,
            expansion_state: "none",
            strength: "moderate",
          }),
        );
      await seedAssessment("2025-01-04");
      checked(
        await admin.from("outcome").insert({
          workspace_id: ws,
          client_id: client,
          event: "renewed",
          occurred_at: "2025-01-05",
          note: "PRIVATE BODY",
        }),
      );
      const signal = required(
        checked(
          await admin
            .from("signal")
            .insert({
              workspace_id: ws,
              client_id: client,
              type: "reduced_engagement",
              severity: "medium",
              basis: "metadata",
              inputs: { messages_30: 1 },
              occurred_at: "2025-01-06T00:00:00Z",
              dedupe_key: "volume",
              rule_version: "test",
            })
            .select("id")
            .single(),
        ),
      ).id;
      checked(
        await admin.from("signal_event").insert({
          workspace_id: ws,
          client_id: client,
          signal_id: signal,
          change: "opened",
          severity: "medium",
          as_of: "2025-01-06T00:00:00Z",
          rule_version: "test",
          inputs: { quote: "PRIVATE BODY" },
        }),
      );
      const caller = createClient<Database>(SUPABASE_URL, PUBLISHABLE_KEY, {
        auth: { persistSession: false },
        global: { headers: { Authorization: `Bearer ${owner.token}` } },
      });
      checked(
        await caller.rpc("set_client_policy", { p_client_id: excluded, p_policy: "excluded" }),
      );
      const app = appFor();
      const root = `/v1/workspaces/${ws}`;
      const headers = { Authorization: `Bearer ${owner.token}` };
      let successes = 0;
      async function read(url: string, token = owner.token) {
        const res = await app.request(url, { headers: { Authorization: `Bearer ${token}` } });
        expect(res.status).toBe(200);
        successes++;
        return res.json();
      }
      const timeline = timelineResponse.parse(await read(`${root}/clients/${client}/timeline`));
      expect(timeline.items.map((e) => e.kind)).toEqual([
        "signal",
        "outcome",
        "state_change",
        "commitment",
        "commitment",
        "commitment",
      ]);
      expect(JSON.stringify(timeline)).not.toContain("PRIVATE BODY");
      const first = evidenceResponse.parse(
        await read(`${root}/clients/${client}/evidence?limit=1`),
      );
      await seedEvidence("2025-01-07T00:00:00Z");
      const second = evidenceResponse.parse(
        await read(
          `${root}/clients/${client}/evidence?limit=100&cursor=${required(first.next_cursor)}`,
        ),
      );
      expect(new Set([...first.items, ...second.items].map((e) => e.id))).toEqual(
        new Set(evidenceIds),
      );
      expect(second.next_cursor).toBeNull();
      const single = evidenceItem.parse(await read(`${root}/evidence/${required(evidenceIds[0])}`));
      expect(single.subject).toHaveLength(120);
      expect(single.author_name).toBe("Synthetic stakeholder");
      expect(single.author_role).toBe("CEO");
      const timelineFirst = timelineResponse.parse(
        await read(`${root}/clients/${client}/timeline?limit=1`),
      );
      await seedAssessment("2025-01-08");
      const timelineRest = timelineResponse.parse(
        await read(
          `${root}/clients/${client}/timeline?cursor=${required(timelineFirst.next_cursor)}`,
        ),
      );
      expect([...timelineFirst.items, ...timelineRest.items].map((e) => e.id)).toEqual(
        timeline.items.map((e) => e.id),
      );
      for (const [url, schema] of [
        [`${root}/clients/${client}/timeline`, timelineResponse],
        [`${root}/clients/${client}/evidence`, evidenceResponse],
        [`${root}/evidence/${required(evidenceIds[0])}`, evidenceItem],
      ] as const) {
        schema.parse(await read(url, administrator.token));
        for (const user of [outsider, contributor]) {
          const res = await app.request(url, {
            headers: { Authorization: `Bearer ${user.token}` },
          });
          expect(res.status).toBe(403);
          apiError.parse(await res.json());
        }
      }
      for (const id of [foreign, excluded, randomUUID()])
        for (const suffix of ["timeline", "evidence"])
          expect((await app.request(`${root}/clients/${id}/${suffix}`, { headers })).status).toBe(
            404,
          );
      for (const id of [hiddenEvidence, randomUUID()])
        expect((await app.request(`${root}/evidence/${id}`, { headers })).status).toBe(404);
      // A foreign evidence UUID must not become visible through the single-item endpoint.
      const foreignConnection = required(
        checked(
          await admin
            .from("connection")
            .insert({
              workspace_id: other,
              provider: "email_upload",
              mode: "upload",
              account_label: "Foreign upload",
            })
            .select("id")
            .single(),
        ),
      ).id;
      const foreignRecord = required(
        checked(
          await admin
            .from("source_record")
            .insert({
              workspace_id: other,
              client_id: foreign,
              connection_id: foreignConnection,
              provider: "email_upload",
              kind: "email",
              direction: "in",
              policy_at_ingest: "full",
              external_id: randomUUID(),
              occurred_at: "2025-01-01T00:00:00Z",
            })
            .select("id")
            .single(),
        ),
      ).id;
      const foreignEvidence = required(
        checked(
          await admin
            .from("evidence")
            .insert({
              workspace_id: other,
              client_id: foreign,
              source_record_id: foreignRecord,
              quote: "Foreign synthetic evidence quote",
              locator: { start: 0, end: 32 },
            })
            .select("id")
            .single(),
        ),
      ).id;
      expect((await app.request(`${root}/evidence/${foreignEvidence}`, { headers })).status).toBe(
        404,
      );
      expect(
        (await app.request(`${root}/clients/${client}/timeline?cursor=bad`, { headers })).status,
      ).toBe(422);
      checked(
        await caller.rpc("set_client_policy", { p_client_id: client, p_policy: "metadata_only" }),
      );
      for (const table of ["evidence", "commitment", "account_assessment"] as const)
        expect(
          checked(
            await admin.from(table).select("id").eq("workspace_id", ws).eq("client_id", client),
          ),
        ).toEqual([]);
      await seedAssessment("2025-01-09");
      const metadataTimeline = timelineResponse.parse(
        await read(`${root}/clients/${client}/timeline`),
      );
      expect(metadataTimeline.items.map((e) => e.kind)).toEqual([
        "state_change",
        "signal",
        "outcome",
      ]);
      expect(metadataTimeline.items.find((e) => e.kind === "signal")).toMatchObject({
        evidence_ids: [],
      });
      expect(JSON.stringify(metadataTimeline)).not.toContain("PRIVATE BODY");
      expect(evidenceResponse.parse(await read(`${root}/clients/${client}/evidence`))).toEqual({
        items: [],
        next_cursor: null,
      });
      expect(
        (await app.request(`${root}/evidence/${required(evidenceIds[0])}`, { headers })).status,
      ).toBe(404);
      const audits = checked(
        await admin
          .from("audit_log")
          .select("action, target_type, target_id, metadata, actor_id")
          .eq("workspace_id", ws)
          .in("action", ["read.client_timeline", "read.client_evidence", "read.evidence"]),
      );
      expect(audits).toHaveLength(successes);
      for (const audit of required(audits)) {
        expect([owner.id, administrator.id]).toContain(audit.actor_id);
        expect(Object.keys(audit.metadata as object)).toEqual(["count"]);
        expect(audit.target_type).toBe(audit.action === "read.evidence" ? "evidence" : "client");
      }
    } finally {
      for (const ws of workspaces.reverse())
        checked(await admin.from("workspace").delete().eq("id", ws));
      for (const user of users.reverse())
        checked<unknown>(await admin.auth.admin.deleteUser(user.id));
    }
  });
});

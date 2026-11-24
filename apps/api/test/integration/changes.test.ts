/** Changes feed isolation and policy against local Supabase, synthetic rows only. */
import { changesResponse } from "@firra/contracts";
import { describe, expect, it } from "vitest";
import { admin, appFor, userWithToken, workspace } from "./read-helpers.js";

const today = new Date().toISOString().slice(0, 10);
async function seed(ws: string, name: string, policy: "full" | "metadata_only" | "excluded") {
  const { data, error } = await admin
    .from("client")
    .insert({ workspace_id: ws, name, policy })
    .select("id")
    .single();
  if (error) throw new Error("client fixture failed");
  const assessment = await admin.from("account_assessment").insert({
    workspace_id: ws,
    client_id: data.id,
    as_of: today,
    run_kind: "live",
    assessment_version: "test",
    rule_version: "test",
    previous_risk_state: "stable",
    risk_state: "watch",
    state_since: today,
    expansion_state: "none",
    strength: "weak",
    play: "monitor",
  });
  if (assessment.error) throw new Error("assessment fixture failed");
  const signal = await admin
    .from("signal")
    .insert({
      workspace_id: ws,
      client_id: data.id,
      type: "reduced_engagement",
      family: "engagement",
      severity: "low",
      basis: "metadata",
      inputs: { messages_30: 1, baseline_per_30: 10 },
      occurred_at: `${today}T00:00:00Z`,
      rule_version: "test",
      dedupe_key: "test",
    })
    .select("id")
    .single();
  if (signal.error) throw new Error("signal fixture failed");
  const event = await admin.from("signal_event").insert({
    workspace_id: ws,
    client_id: data.id,
    signal_id: signal.data.id,
    change: "opened",
    severity: "low",
    as_of: `${today}T00:00:00Z`,
    rule_version: "test",
  });
  if (event.error) throw new Error("event fixture failed");
}

describe("changes feed (local Supabase)", () => {
  it("isolates workspaces, hides excluded clients, includes metadata without text, and audits", async () => {
    const workspaces: string[] = [];
    let ownerId: string | undefined;
    try {
      const ws = await workspace();
      workspaces.push(ws);
      const other = await workspace();
      workspaces.push(other);
      const owner = await userWithToken("changes-owner");
      ownerId = owner.id;
      const membership = await admin
        .from("membership")
        .insert({ workspace_id: ws, user_id: owner.id, role: "owner" });
      if (membership.error) throw new Error("membership fixture failed");
      await seed(ws, "Visible Full", "full");
      await seed(ws, "Visible Metadata", "metadata_only");
      await seed(ws, "Hidden Excluded", "excluded");
      await seed(other, "Other Workspace", "full");
      const app = appFor();
      const headers = { Authorization: `Bearer ${owner.token}` };
      const response = await app.request(`/v1/workspaces/${ws}/changes?since=${today}`, {
        headers,
      });
      expect(response.status).toBe(200);
      const body = changesResponse.parse(await response.json());
      expect(body.items).toHaveLength(4);
      expect(new Set(body.items.map((i) => i.client_name))).toEqual(
        new Set(["Visible Full", "Visible Metadata"]),
      );
      expect(
        body.items.every((i) => i.evidence_count >= 0 && !("text" in i) && !("quote" in i)),
      ).toBe(true);
      expect((await app.request(`/v1/workspaces/${other}/changes`, { headers })).status).toBe(403);
      const audit = await admin
        .from("audit_log")
        .select("actor_id, metadata")
        .eq("workspace_id", ws)
        .eq("action", "read.changes");
      expect(audit.data).toEqual([{ actor_id: owner.id, metadata: { count: 4 } }]);
    } finally {
      for (const ws of workspaces) await admin.from("workspace").delete().eq("id", ws);
      if (ownerId) await admin.auth.admin.deleteUser(ownerId);
    }
  });
});

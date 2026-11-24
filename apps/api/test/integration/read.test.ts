/** Caller-token attention reads against local Supabase. */
import { attentionResponse } from "@firra/contracts";
import { describe, expect, it } from "vitest";
import { admin, appFor, userWithToken, workspace } from "./read-helpers.js";

async function client(
  workspaceId: string,
  name: string,
  policy: "full" | "metadata_only" | "excluded",
  risk: "high" | "elevated" | "stable",
): Promise<string> {
  const { data } = await admin
    .from("client")
    .insert({ workspace_id: workspaceId, name, policy })
    .select("id")
    .single();
  if (!data) throw new Error("no client");
  const today = new Date().toISOString().slice(0, 10);
  const { error } = await admin.from("account_assessment").insert({
    workspace_id: workspaceId,
    client_id: data.id,
    as_of: today,
    run_kind: "live",
    assessment_version: "test",
    rule_version: "test",
    risk_state: risk,
    state_since: today,
    expansion_state: "none",
    strength: "moderate",
    play: "monitor",
    drivers: [
      {
        type: "delivery_complaint",
        family: "delivery",
        severity: "high",
        basis: "evidence",
        occurred_at: `${today}T08:00:00+00:00`,
        primary: true,
        evidence_ids: [],
      },
    ],
  });
  if (error) throw new Error(`assessment insert failed: ${error.message}`);
  return data.id;
}

describe("GET /v1/workspaces/:id/attention (local Supabase)", () => {
  it("shows visible clients only, refuses outsiders and audits the read", async () => {
    const ws = await workspace();
    const other = await workspace();
    const owner = await userWithToken("owner");
    const outsider = await userWithToken("outsider");
    await admin.from("membership").insert({ workspace_id: ws, user_id: owner.id, role: "owner" });
    await admin
      .from("membership")
      .insert({ workspace_id: other, user_id: outsider.id, role: "owner" });
    await client(ws, "Visible Full", "full", "high");
    await client(ws, "Visible Metadata", "metadata_only", "elevated");
    await client(ws, "Hidden Excluded", "excluded", "high");
    await client(ws, "Calm", "full", "stable");
    await client(other, "Other Workspace", "full", "high");

    const app = appFor();
    const res = await app.request(`/v1/workspaces/${ws}/attention`, {
      headers: { Authorization: `Bearer ${owner.token}` },
    });
    expect(res.status).toBe(200);
    const body = attentionResponse.parse(await res.json());
    expect(body.items.map((i) => i.name)).toEqual(["Visible Full", "Visible Metadata"]);
    expect(JSON.stringify(body)).not.toContain("Hidden Excluded");

    const foreign = await app.request(`/v1/workspaces/${ws}/attention`, {
      headers: { Authorization: `Bearer ${outsider.token}` },
    });
    expect(foreign.status).toBe(403);

    const { data: audit } = await admin
      .from("audit_log")
      .select("actor_id, action, metadata")
      .eq("workspace_id", ws)
      .eq("action", "read.attention");
    expect(audit).toEqual([
      { actor_id: owner.id, action: "read.attention", metadata: { count: 2 } },
    ]);
  });
});

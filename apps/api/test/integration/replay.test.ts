/** Replay reads over real caller RLS and policy deletion (synthetic local fixtures). */
import { randomUUID } from "node:crypto";
import type { ReplayDatabase } from "../../src/lib/replay-schema.js";
import type { SupabaseClient } from "@supabase/supabase-js";
import { createClient } from "@supabase/supabase-js";
import { replayClientResponse, replayRunsResponse, apiError } from "@firra/contracts";
import { expect, it } from "vitest";
import { required } from "../client-fixtures.js";
import { NARRATIVE, RUN } from "../replay-fixtures.js";
import {
  admin,
  appFor,
  userWithToken,
  workspace,
  SUPABASE_URL,
  PUBLISHABLE_KEY,
} from "./read-helpers.js";
function checked<T>(result: { data: T; error: unknown }): T {
  if (result.error) throw new Error("synthetic replay fixture failed");
  return result.data;
}
it("isolates roles, workspaces and runs; purges narratives on downgrade/exclusion; audits successful reads", async () => {
  const workspaces: string[] = [];
  const users: { id: string }[] = [];
  try {
    const ws = await workspace();
    workspaces.push(ws);
    const other = await workspace();
    workspaces.push(other);
    const owner = await userWithToken("replay-owner");
    users.push(owner);
    const administrator = await userWithToken("replay-admin");
    users.push(administrator);
    const outsider = await userWithToken("replay-outsider");
    users.push(outsider);
    const contributor = await userWithToken("replay-contributor");
    users.push(contributor);
    checked(
      await admin.from("membership").insert([
        { workspace_id: ws, user_id: owner.id, role: "owner" },
        { workspace_id: ws, user_id: administrator.id, role: "admin" },
        { workspace_id: other, user_id: outsider.id, role: "owner" },
      ]),
    );
    checked(await admin.from("contributor").insert({ workspace_id: ws, email: contributor.email }));
    const client = required(
      checked(
        await admin
          .from("client")
          .insert({ workspace_id: ws, name: "Synthetic replay", policy: "full" })
          .select("id")
          .single(),
      ),
    ).id;
    const foreignClient = required(
      checked(
        await admin
          .from("client")
          .insert({ workspace_id: other, name: "Synthetic foreign", policy: "full" })
          .select("id")
          .single(),
      ),
    ).id;
    const makeRun = async (workspaceId: string) =>
      required(
        checked(
          await admin
            .from("replay_run")
            .insert({
              workspace_id: workspaceId,
              window_start: RUN.window_start,
              window_end: RUN.window_end,
              status: "succeeded",
              finished_at: RUN.finished_at,
              summary: { totals: RUN.totals },
            })
            .select("id")
            .single(),
        ),
      ).id;
    const run = await makeRun(ws);
    const legacy = await makeRun(ws);
    const foreignRun = await makeRun(other);
    // Generated db-types predate migration 19; every returned payload is contract-validated.
    const narratives = admin as unknown as SupabaseClient<ReplayDatabase>;
    checked(
      await narratives.from("replay_narrative").insert([
        {
          workspace_id: ws,
          replay_run_id: run,
          client_id: client,
          version: 1,
          narrative: NARRATIVE,
        },
        {
          workspace_id: other,
          replay_run_id: foreignRun,
          client_id: foreignClient,
          version: 1,
          narrative: NARRATIVE,
        },
      ]),
    );
    const app = appFor();
    const root = `/v1/workspaces/${ws}/replay/runs`;
    const request = (suffix: string, token = owner.token) =>
      app.request(root + suffix, { headers: { Authorization: `Bearer ${token}` } });
    const detail = `/${run}/clients/${client}`;
    for (const token of [owner.token, administrator.token]) {
      const list = await request("", token);
      expect(list.status).toBe(200);
      const items = replayRunsResponse.parse(await list.json()).items;
      expect(items.find((r) => r.id === run)?.narrated).toBe(true);
      expect(items.find((r) => r.id === legacy)?.narrated).toBe(false);
      expect(items.some((r) => r.id === foreignRun)).toBe(false);
      const res = await request(detail, token);
      expect(res.status).toBe(200);
      expect(replayClientResponse.parse(await res.json()).narrative).toEqual(NARRATIVE);
    }
    const audits = required(
      checked(
        await admin
          .from("audit_log")
          .select("action, target_id")
          .eq("workspace_id", ws)
          .in("action", ["read.replay_runs", "read.replay_client"]),
      ),
    );
    expect(audits).toHaveLength(4);
    expect(
      audits.filter((a) => a.action === "read.replay_client").every((a) => a.target_id === client),
    ).toBe(true);
    for (const token of [outsider.token, contributor.token]) {
      for (const suffix of ["", detail]) expect((await request(suffix, token)).status).toBe(403);
    }
    for (const suffix of [
      `/${legacy}/clients/${client}`,
      `/${foreignRun}/clients/${client}`,
      `/${run}/clients/${foreignClient}`,
      `/${randomUUID()}/clients/${client}`,
    ]) {
      const res = await request(suffix);
      expect(res.status).toBe(404);
      apiError.parse(await res.json());
    }
    const caller = createClient(SUPABASE_URL, PUBLISHABLE_KEY, {
      auth: { persistSession: false },
      global: { headers: { Authorization: `Bearer ${owner.token}` } },
    });
    checked(
      await caller.rpc("set_client_policy", { p_client_id: client, p_policy: "metadata_only" }),
    );
    expect((await request(detail)).status).toBe(404);
    expect(
      checked(await caller.from("replay_narrative").select("id").eq("workspace_id", ws)),
    ).toHaveLength(0);
    checked(
      await narratives.from("replay_narrative").insert({
        workspace_id: ws,
        replay_run_id: run,
        client_id: client,
        version: 1,
        narrative: { ...NARRATIVE, visible_at_detection: [] },
      }),
    );
    const metadata = await request(detail);
    expect(metadata.status).toBe(200);
    expect(replayClientResponse.parse(await metadata.json()).evidence_ids).toEqual([]);
    checked(await caller.rpc("set_client_policy", { p_client_id: client, p_policy: "excluded" }));
    expect((await request(detail)).status).toBe(404);
    expect(
      checked(await narratives.from("replay_narrative").select("id").eq("workspace_id", ws)),
    ).toHaveLength(0);
    expect(
      checked(await caller.from("replay_narrative").select("id").eq("workspace_id", other)),
    ).toHaveLength(0);
    const finalAudits = checked(
      await admin
        .from("audit_log")
        .select("id")
        .eq("workspace_id", ws)
        .in("action", ["read.replay_runs", "read.replay_client"]),
    );
    expect(finalAudits).toHaveLength(5);
  } finally {
    for (const id of workspaces) await admin.from("workspace").delete().eq("id", id);
    for (const user of users) await admin.auth.admin.deleteUser(user.id);
  }
}, 30000);

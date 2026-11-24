/** Local synthetic proof of caller RLS, routine upserts, last runs and exactly-once audits. */
import { createClient } from "@supabase/supabase-js";
import type { Database } from "@firra/db-types";
import { mutationResponse, routineKind, routinesResponse } from "@firra/contracts";
import { expect, it } from "vitest";
import {
  admin,
  appFor,
  PUBLISHABLE_KEY,
  SUPABASE_URL,
  userWithToken,
  workspace,
} from "./read-helpers.js";

it("lists lazy defaults and real newest runs, switches as owner/admin and isolates caller access", async () => {
  const owner = await userWithToken("routines-owner");
  const manager = await userWithToken("routines-admin");
  const outsider = await userWithToken("routines-outsider");
  const foreign = await userWithToken("routines-foreign");
  const contributor = await userWithToken("routines-contributor");
  const ws = await workspace();
  const other = await workspace();
  const app = appFor();
  const base = `/v1/workspaces/${ws}/routines`;
  const headers = (token: string) => ({
    authorization: `Bearer ${token}`,
    "content-type": "application/json",
  });
  try {
    expect(
      (
        await admin.from("membership").insert([
          { workspace_id: ws, user_id: owner.id, role: "owner" },
          { workspace_id: ws, user_id: manager.id, role: "admin" },
          { workspace_id: other, user_id: foreign.id, role: "owner" },
        ])
      ).error,
    ).toBeNull();
    expect(
      (await admin.from("contributor").insert({ workspace_id: ws, email: contributor.email }))
        .error,
    ).toBeNull();
    const initial = await app.request(base, { headers: headers(owner.token) });
    expect(initial.status).toBe(200);
    expect(initial.headers.get("cache-control")).toBe("no-store");
    expect(routinesResponse.parse(await initial.json()).items).toEqual(
      routineKind.options.map((kind) => ({ kind, created: false, enabled: true, last_run: null })),
    );
    let id = "";
    for (const [token, enabled] of [
      [owner.token, false],
      [manager.token, true],
    ] as const) {
      const response = await app.request(`${base}/morning_scan`, {
        method: "PUT",
        headers: headers(token),
        body: JSON.stringify({ enabled }),
      });
      expect(response.status).toBe(200);
      expect(response.headers.get("cache-control")).toBe("no-store");
      const returned = mutationResponse.parse(await response.json()).id;
      if (id) expect(returned).toBe(id);
      id = returned;
    }
    const watch = await app.request(`${base}/commitment_watch`, {
      method: "PUT",
      headers: headers(manager.token),
      body: '{"enabled":false}',
    });
    expect(watch.status).toBe(200);
    const definitions = await admin.from("routine").select("kind, enabled").eq("workspace_id", ws);
    expect(definitions.data).toHaveLength(2);
    expect(definitions.data).toContainEqual({ kind: "morning_scan", enabled: true });
    expect(definitions.data).toContainEqual({ kind: "commitment_watch", enabled: false });
    // Insert the newest schedule first: creation order must not select the older scheduled run.
    expect(
      (
        await admin.from("routine_run").insert([
          {
            workspace_id: ws,
            routine_id: id,
            scheduled_for: "2026-10-04",
            status: "succeeded",
            finished_at: "2026-10-04T08:00:00Z",
            counts: { items: 4, important: 2, suppressed: 1 },
          },
          {
            workspace_id: ws,
            routine_id: id,
            scheduled_for: "2026-10-03",
            status: "failed",
            counts: { items: 0 },
          },
        ])
      ).error,
    ).toBeNull();
    const list = await app.request(base, { headers: headers(manager.token) });
    expect(list.status).toBe(200);
    const items = routinesResponse.parse(await list.json()).items;
    expect(items.map((r) => r.kind)).toEqual(routineKind.options);
    expect(items[0]).toEqual({
      kind: "morning_scan",
      enabled: true,
      created: true,
      last_run: {
        scheduled_for: "2026-10-04",
        status: "succeeded",
        finished_at: "2026-10-04T08:00:00.000000Z",
        counts: { items: 4, important: 2, suppressed: 1 },
      },
    });
    expect(items[1]).toEqual({
      kind: "commitment_watch",
      enabled: false,
      created: true,
      last_run: null,
    });
    expect(items[2]).toEqual({
      kind: "pre_meeting_brief",
      enabled: true,
      created: false,
      last_run: null,
    });
    for (const token of [outsider.token, foreign.token, contributor.token]) {
      expect((await app.request(base, { headers: headers(token) })).status).toBe(403);
      expect(
        (
          await app.request(`${base}/morning_scan`, {
            method: "PUT",
            headers: headers(token),
            body: '{"enabled":false}',
          })
        ).status,
      ).toBe(403);
    }
    for (const method of ["GET", "PUT"])
      expect(
        (
          await app.request(
            `/v1/workspaces/${other}/routines${method === "PUT" ? "/morning_scan" : ""}`,
            {
              method,
              headers: headers(owner.token),
              ...(method === "PUT" ? { body: '{"enabled":true}' } : {}),
            },
          )
        ).status,
      ).toBe(403);
    for (const [kind, body] of [
      ["unknown", '{"enabled":true}'],
      ["morning_scan", '{"enabled":"true"}'],
    ])
      expect(
        (
          await app.request(`${base}/${kind}`, {
            method: "PUT",
            headers: headers(owner.token),
            body,
          })
        ).status,
      ).toBe(422);
    const caller = createClient<Database>(SUPABASE_URL, PUBLISHABLE_KEY, {
      auth: { persistSession: false, autoRefreshToken: false },
      global: { headers: { authorization: `Bearer ${outsider.token}` } },
    });
    expect((await caller.from("routine_run").select("id").eq("workspace_id", ws)).data).toEqual([]);
    expect(
      (
        await caller.rpc("set_routine_enabled", {
          p_workspace_id: ws,
          p_kind: "morning_scan",
          p_enabled: false,
        })
      ).error?.code,
    ).toBe("42501");
    const audit = await admin
      .from("audit_log")
      .select("action, actor_id, target_type, target_id, metadata")
      .eq("workspace_id", ws);
    expect(audit.error).toBeNull();
    expect(audit.data?.filter((a) => a.action === "read.routines")).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ actor_id: owner.id, metadata: { count: 3 } }),
        expect.objectContaining({ actor_id: manager.id, metadata: { count: 3 } }),
      ]),
    );
    expect(audit.data?.map((a) => a.action).sort()).toEqual(
      [
        "read.routines",
        "read.routines",
        "routine.enabled_changed",
        "routine.enabled_changed",
        "routine.enabled_changed",
      ].sort(),
    );
    expect(
      audit.data?.filter((a) => a.action === "routine.enabled_changed" && a.target_id === id),
    ).toHaveLength(2);
  } finally {
    await admin.from("workspace").delete().in("id", [ws, other]);
    for (const user of [owner, manager, outsider, foreign, contributor])
      await admin.auth.admin.deleteUser(user.id);
  }
}, 30000);

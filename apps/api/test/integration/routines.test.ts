/**
 * Synthetic local Supabase proof for T50e: `GET /v1/workspaces/:id/routines` always lists the three
 * kinds in order (a kind with no row is `created: false, enabled: true`, `last_run` null), the read
 * is owner/admin only and audited, and `PUT /v1/workspaces/:id/routines/:kind` switches a routine
 * through the audited `set_routine_enabled` RPC (upsert, no duplicate, non-member is 403).
 *
 * Written from the original task spec; the response shape is asserted field by field
 * so the oracle does not depend on a contract export name that the brief does not fix.
 */
import { mutationResponse } from "@firra/contracts";
import { expect, it } from "vitest";
import { admin, appFor, userWithToken, workspace } from "./read-helpers.js";

interface RoutineRun {
  scheduled_for: string;
  status: string;
  finished_at: string | null;
  counts: Record<string, number>;
}

interface RoutineItem {
  kind: string;
  enabled: boolean;
  created: boolean;
  last_run: RoutineRun | null;
}

interface RoutinesBody {
  generated_at: string;
  items: RoutineItem[];
}

const KINDS = ["morning_scan", "commitment_watch", "pre_meeting_brief"];

function parseRoutines(value: unknown): RoutinesBody {
  if (typeof value !== "object" || value === null)
    throw new Error("routines body is not an object");
  const body = value as { generated_at?: unknown; items?: unknown };
  expect(typeof body.generated_at).toBe("string");
  expect(Number.isNaN(Date.parse(String(body.generated_at)))).toBe(false);
  if (!Array.isArray(body.items)) throw new Error("routines items missing");
  for (const item of body.items) {
    if (typeof item !== "object" || item === null) throw new Error("routine item malformed");
    const row = item as Record<string, unknown>;
    expect(typeof row.kind).toBe("string");
    expect(typeof row.enabled).toBe("boolean");
    expect(typeof row.created).toBe("boolean");
    if (row.last_run !== null && row.last_run !== undefined) {
      if (typeof row.last_run !== "object") throw new Error("last_run malformed");
      const run = row.last_run as Record<string, unknown>;
      expect(typeof run.scheduled_for).toBe("string");
      expect(typeof run.status).toBe("string");
      expect(run.finished_at === null || typeof run.finished_at === "string").toBe(true);
      expect(typeof run.counts).toBe("object");
    } else {
      expect(row.last_run).toBeNull();
    }
  }
  return value as RoutinesBody;
}

it("lists three routines and switches one through the audited RPC", async () => {
  const owner = await userWithToken("routines-owner");
  const adminUser = await userWithToken("routines-admin");
  const outsider = await userWithToken("routines-outsider");
  const contributor = await userWithToken("routines-contributor");
  const foreign = await userWithToken("routines-foreign");
  const ws = await workspace();
  const foreignWs = await workspace();
  const app = appFor();
  const base = `/v1/workspaces/${ws}`;
  const list = async (token: string): Promise<RoutinesBody> => {
    const res = await app.request(`${base}/routines`, {
      headers: { authorization: `Bearer ${token}` },
    });
    expect(res.status).toBe(200);
    return parseRoutines(await res.json());
  };
  const put = (kind: string, body: string, token = owner.token, contentType = "application/json") =>
    app.request(`${base}/routines/${kind}`, {
      method: "PUT",
      headers: { authorization: `Bearer ${token}`, "content-type": contentType },
      body,
    });
  try {
    expect(
      (
        await admin.from("membership").insert([
          { workspace_id: ws, user_id: owner.id, role: "owner" },
          { workspace_id: ws, user_id: adminUser.id, role: "admin" },
          { workspace_id: foreignWs, user_id: foreign.id, role: "owner" },
        ])
      ).error,
    ).toBeNull();
    expect(
      (await admin.from("contributor").insert({ workspace_id: ws, email: contributor.email }))
        .error,
    ).toBeNull();

    // No routine row yet: all three kinds are listed, created false / enabled true / last_run null.
    const initial = await list(owner.token);
    expect(initial.items.map((item) => item.kind)).toEqual(KINDS);
    expect(
      initial.items.every((item) => !item.created && item.enabled && item.last_run === null),
    ).toBe(true);

    // The newest routine_run (by scheduled_for) is the last_run the read reports.
    const routine = await admin
      .from("routine")
      .insert({
        workspace_id: ws,
        kind: "morning_scan",
        enabled: true,
        definition_version: "routines.v1",
      })
      .select("id")
      .single();
    if (!routine.data) throw new Error("synthetic routine failed");
    expect(
      (
        await admin.from("routine_run").insert([
          {
            workspace_id: ws,
            routine_id: routine.data.id,
            scheduled_for: "2026-10-01",
            status: "succeeded",
            counts: { items: 2, important: 1, demoted: 0, suppressed: 0, skipped_no_assessment: 0 },
            finished_at: "2026-10-01T07:31:00Z",
          },
          {
            workspace_id: ws,
            routine_id: routine.data.id,
            scheduled_for: "2026-10-02",
            status: "failed",
            counts: {},
            error_class: "ValueError",
            finished_at: "2026-10-02T08:01:00Z",
          },
        ])
      ).error,
    ).toBeNull();
    const seeded = await list(owner.token);
    const morning = seeded.items.find((item) => item.kind === "morning_scan");
    expect(morning).toMatchObject({
      kind: "morning_scan",
      enabled: true,
      created: true,
      last_run: { scheduled_for: "2026-10-02", status: "failed", counts: {} },
    });
    expect(Date.parse(String(morning?.last_run?.finished_at))).toBe(
      Date.parse("2026-10-02T08:01:00Z"),
    );

    // Owner and admin read; contributor and outsider are refused by the owner/admin guard.
    expect((await list(adminUser.token)).items).toHaveLength(3);
    for (const user of [contributor, outsider]) {
      const res = await app.request(`${base}/routines`, {
        headers: { authorization: `Bearer ${user.token}` },
      });
      expect(res.status).toBe(403);
    }

    // Refused before the RPC: unknown kind, bad body and a non-JSON content type.
    expect((await put("pre_meeting", '{"enabled":false}')).status).toBe(422);
    expect((await put("morning_scan", "{}")).status).toBe(422);
    expect((await put("morning_scan", '{"enabled":"no"}')).status).toBe(422);
    expect((await put("morning_scan", '{"enabled":false}', owner.token, "text/plain")).status).toBe(
      422,
    );

    // A non-member never reaches the RPC: the workspace guard answers 403 (the RPC's own forbidden
    // maps to 404 and is covered by the unit test with a stubbed store, no role can reach it here).
    expect((await put("morning_scan", '{"enabled":false}', foreign.token)).status).toBe(403);

    // The first PUT creates the row through the RPC; the second changes it and does not duplicate it.
    const created = await put("pre_meeting_brief", '{"enabled":false}');
    expect(created.status).toBe(200);
    const createdId = mutationResponse.parse(await created.json()).id;
    const updated = await put("pre_meeting_brief", '{"enabled":true}', adminUser.token);
    expect(updated.status).toBe(200);
    expect(mutationResponse.parse(await updated.json()).id).toBe(createdId);
    const rows = await admin
      .from("routine")
      .select("id, enabled")
      .eq("workspace_id", ws)
      .eq("kind", "pre_meeting_brief");
    expect(rows.error).toBeNull();
    expect(rows.data).toEqual([{ id: createdId, enabled: true }]);

    const final = await list(owner.token);
    expect(final.items.find((item) => item.kind === "pre_meeting_brief")).toMatchObject({
      created: true,
      enabled: true,
    });

    // One `read.routines` per successful read and one `routine.enabled_changed` per write (the RPC
    // writes it; the route must not add a second one). Refused requests leave no audit row.
    const audit = await admin.from("audit_log").select("action").eq("workspace_id", ws);
    expect(audit.error).toBeNull();
    expect(audit.data?.map((row) => row.action).sort()).toEqual(
      [
        "read.routines",
        "read.routines",
        "read.routines",
        "read.routines",
        "routine.enabled_changed",
        "routine.enabled_changed",
      ].sort(),
    );
  } finally {
    await admin.from("workspace").delete().in("id", [ws, foreignWs]);
    for (const user of [owner, adminUser, outsider, contributor, foreign])
      await admin.auth.admin.deleteUser(user.id);
  }
}, 30000);

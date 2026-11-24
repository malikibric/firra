/** Setup API against local Supabase in CI: real caller RPCs, tenant isolation and RPC-owned audit. */
import { randomUUID } from "node:crypto";
import {
  adminInviteResponse,
  contributorInviteResponse,
  setupIdResponse,
  setupReadRoutes,
} from "@firra/contracts";
import { describe, expect, it } from "vitest";
import { admin, appFor, userWithToken } from "./read-helpers.js";

describe("setup API (local Supabase)", () => {
  it("runs every write family as the caller, preserves workspace boundaries, and records RPC audits", async () => {
    const app = appFor();
    const owner = await userWithToken("setup-owner");
    const member = await userWithToken("setup-admin");
    const outsider = await userWithToken("setup-outsider");
    const contributor = await userWithToken("setup-contributor");
    const auth = (token: string) => ({ Authorization: `Bearer ${token}` });
    const request = (path: string, method: string, body?: unknown, token = owner.token) =>
      app.request(`/v1${path}`, {
        method,
        headers: {
          ...auth(token),
          ...(body === undefined ? {} : { "content-type": "application/json" }),
        },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
    const create = async (path: string, body: unknown) => {
      const response = await request(path, "POST", body);
      expect(response.status).toBe(200);
      return setupIdResponse.parse(await response.json()).id;
    };
    const ws = await create("/workspaces", {
      name: "Setup integration",
      timezone: "UTC",
      default_locale: "en",
    });
    const otherWs = await create("/workspaces", { name: "Other integration" });
    const base = `/workspaces/${ws}`;
    const clientId = await create(`${base}/clients`, {
      name: "Synthetic client",
      domains: [`fixture-${randomUUID()}.test`],
    });
    expect(
      (
        await request(`${base}/clients/${clientId}`, "PATCH", {
          name: "Renamed synthetic",
          status: "churned",
        })
      ).status,
    ).toBe(204);
    expect(
      (await request(`${base}/clients/${clientId}/domains`, "PUT", { domains: ["synthetic.test"] }))
        .status,
    ).toBe(204);
    expect(
      (
        await request(`${base}/clients/${clientId}/policy`, "PUT", {
          policy: "excluded",
          confirm_exclusion: true,
        })
      ).status,
    ).toBe(204);
    // Excluded setup metadata can be edited; intelligence endpoints still hide it.
    expect((await request(`${base}/clients/${clientId}`, "GET")).status).toBe(404);
    const setupClients = await request(`${base}/setup/clients`, "GET");
    expect(setupClients.status).toBe(200);
    expect(await setupClients.json()).toMatchObject({
      items: [{ id: clientId, policy: "excluded" }],
    });
    expect(
      (await request(`${base}/clients/${clientId}/policy`, "PUT", { policy: "metadata_only" }))
        .status,
    ).toBe(204);
    const svc = await create(`${base}/services`, {
      name: "Synthetic service",
      keywords: [" CRM ", "crm"],
    });
    const saved = await request(`${base}/services/${svc}`, "PUT", {
      name: "Updated service",
      description: "Synthetic description",
      keywords: ["strategy"],
    });
    expect(saved.status).toBe(200);
    expect(setupIdResponse.parse(await saved.json()).id).toBe(svc);
    expect((await request(`${base}/services/${svc}`, "DELETE")).status).toBe(204);

    const invitation = await request(`${base}/invites`, "POST", {
      email: member.email,
      locale: "en",
    });
    expect(invitation.status).toBe(200);
    const invitationBody: unknown = await invitation.json();
    expect(invitationBody).not.toHaveProperty("link");
    const invited = adminInviteResponse.parse(invitationBody);
    expect(invited.emailed).toBe(false);
    expect(
      (await request("/invites/accept", "POST", { token: invited.token }, outsider.token)).status,
    ).toBe(404);
    const accepted = await request(
      "/invites/accept",
      "POST",
      { token: invited.token },
      member.token,
    );
    expect(accepted.status).toBe(200);
    expect(setupIdResponse.parse(await accepted.json()).id).toBe(ws);
    // The role-specific RPC enforces owner-only removal despite the shared owner/admin route guard.
    expect(
      (await request(`${base}/members/${owner.id}`, "DELETE", undefined, member.token)).status,
    ).toBe(404);
    expect((await request(`${base}/members/${member.id}`, "DELETE")).status).toBe(204);
    const pending = await request(`${base}/invites`, "POST", { email: outsider.email });
    const pendingInvite = adminInviteResponse.parse(await pending.json());
    expect((await request(`${base}/invites/${pendingInvite.id}`, "DELETE")).status).toBe(204);

    const contribResponse = await request(`${base}/contributors`, "POST", {
      email: contributor.email,
      name: "Synthetic contributor",
    });
    expect(contribResponse.status).toBe(200);
    const contributorBody: unknown = await contribResponse.json();
    expect(contributorBody).not.toHaveProperty("link");
    const contrib = contributorInviteResponse.parse(contributorBody);
    const claimed = await request(
      "/contributor/claim",
      "POST",
      { token: contrib.token },
      contributor.token,
    );
    expect(claimed.status).toBe(200);
    const connectionId = randomUUID();
    const secondConnectionId = randomUUID();
    const foreignConnectionId = randomUUID();
    const connectionInsert = await admin.from("connection").insert([
      {
        id: connectionId,
        workspace_id: ws,
        provider: "email_upload",
        mode: "upload",
        account_label: "synthetic-own",
        owner_user_id: owner.id,
        token_ciphertext: "synthetic-token",
      },
      {
        id: secondConnectionId,
        workspace_id: ws,
        provider: "email_upload",
        mode: "upload",
        account_label: "synthetic-contributor",
        owner_contributor_id: contrib.id,
        token_ciphertext: "synthetic-other-token",
      },
      {
        id: foreignConnectionId,
        workspace_id: otherWs,
        provider: "email_upload",
        mode: "upload",
        account_label: "synthetic-foreign",
        token_ciphertext: "synthetic-foreign-token",
      },
    ]);
    expect(connectionInsert.error).toBeNull();
    // Owner has both workspaces: path mismatch is still 404, no audit/mutation in the other workspace.
    expect((await request(`${base}/connections/${foreignConnectionId}`, "DELETE")).status).toBe(
      404,
    );
    expect((await request(`${base}/connections/${randomUUID()}`, "DELETE")).status).toBe(404);
    expect((await request(`${base}/connections/${connectionId}`, "DELETE")).status).toBe(204);
    const disconnected = await admin
      .from("connection")
      .select("id, status, token_ciphertext")
      .in("id", [connectionId, secondConnectionId, foreignConnectionId]);
    expect(disconnected.data?.find((row) => row.id === connectionId)).toMatchObject({
      status: "revoked",
      token_ciphertext: null,
    });
    expect(disconnected.data?.find((row) => row.id === secondConnectionId)).toMatchObject({
      status: "active",
      token_ciphertext: "synthetic-other-token",
    });
    expect(disconnected.data?.find((row) => row.id === foreignConnectionId)).toMatchObject({
      status: "active",
      token_ciphertext: "synthetic-foreign-token",
    });

    for (const route of setupReadRoutes) {
      const path = `${base}${route.path.replace(":clientId", clientId)}`;
      const response = await request(path, "GET");
      expect(response.status).toBe(200);
      route.response.parse(await response.json());
      expect((await request(path, "GET", undefined, outsider.token)).status).toBe(403);
      expect((await request(path, "GET", undefined, contributor.token)).status).toBe(403);
    }
    expect((await request(`${base}/connections`, "GET")).status).toBe(200);
    const listed = JSON.stringify(await (await request(`${base}/connections`, "GET")).json());
    expect(listed).not.toContain("token_ciphertext");
    expect(listed).not.toContain("synthetic-token");
    expect(listed).not.toContain(foreignConnectionId);
    expect((await request(`${base}/contributors/${contrib.id}`, "DELETE")).status).toBe(204);
    const replayId = await create(`${base}/replay/runs`, {
      window_start: "2026-01-01",
      window_end: "2026-02-01",
      step_days: 7,
    });
    const replay = await admin
      .from("replay_run")
      .select("id, workspace_id, requested_by")
      .eq("id", replayId)
      .single();
    expect(replay.data).toMatchObject({ workspace_id: ws, requested_by: owner.id });

    const audit = await admin
      .from("audit_log")
      .select("action, actor_id, target_id")
      .eq("workspace_id", ws);
    expect(audit.error).toBeNull();
    const actions = audit.data?.map((row) => row.action) ?? [];
    for (const action of [
      "workspace.created",
      "client.created",
      "client.updated",
      "client.domains_set",
      "client.policy_changed",
      "service.created",
      "service.updated",
      "service.deleted",
      "workspace_invite.created",
      "workspace_invite.accepted",
      "workspace_invite.revoked",
      "membership.removed",
      "contributor.invited",
      "contributor.revoked",
      "connection.disconnected",
      "replay_run.requested",
    ])
      expect(actions).toContain(action);
    const connectionAudits = audit.data?.filter((row) => row.action === "connection.disconnected");
    expect(connectionAudits).toEqual([
      { action: "connection.disconnected", actor_id: owner.id, target_id: connectionId },
    ]);
    for (const route of setupReadRoutes) expect(actions).toContain(`read.setup_${route.operation}`);
  });
});

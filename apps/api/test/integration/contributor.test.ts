/**
 * Contributor accounts against local Supabase: an invited user claims their invite, sees only their
 * own connections, nothing about the workspace, and loses everything when revoked.
 */
import { randomUUID } from "node:crypto";
import { claimInviteResponse, contributorConnectionsResponse, meResponse } from "@firra/contracts";
import { createClient } from "@supabase/supabase-js";
import type { Database } from "@firra/db-types";
import { describe, expect, it } from "vitest";
import {
  PUBLISHABLE_KEY,
  SUPABASE_URL,
  admin,
  appFor,
  userWithToken,
  workspace,
} from "./read-helpers.js";

describe("contributor accounts (local Supabase)", () => {
  it("claims an invite, sees only own connections, and stops after revocation", async () => {
    const ws = await workspace();
    const owner = await userWithToken("owner");
    const contributor = await userWithToken("contrib");
    await admin.from("membership").insert({ workspace_id: ws, user_id: owner.id, role: "owner" });
    const ownerDb = createClient<Database>(SUPABASE_URL, PUBLISHABLE_KEY, {
      auth: { persistSession: false },
      global: { headers: { Authorization: `Bearer ${owner.token}` } },
    });
    const invite = await ownerDb.rpc("create_contributor_invite", {
      p_workspace_id: ws,
      p_email: contributor.email,
      p_name: "Cee",
    });
    const code = invite.data?.[0]?.token ?? "";
    expect(code).toMatch(/^[0-9a-f]{64}$/);
    const { data: row } = await admin
      .from("contributor")
      .select("id")
      .eq("workspace_id", ws)
      .eq("email", contributor.email)
      .single();
    const contributorId = row?.id ?? "";
    await admin.from("connection").insert([
      {
        workspace_id: ws,
        provider: "gmail",
        mode: "user",
        account_label: "owner-box",
        owner_user_id: owner.id,
        external_account_id: `o-${randomUUID()}@example.com`,
        status: "active",
      },
      {
        workspace_id: ws,
        provider: "gmail",
        mode: "user",
        account_label: "contrib-box",
        owner_contributor_id: contributorId,
        external_account_id: `c-${randomUUID()}@example.com`,
        status: "active",
      },
    ]);

    const app = appFor();
    const auth = (token: string) => ({ Authorization: `Bearer ${token}` });
    const post = (url: string, token: string, body: unknown) =>
      app.request(url, {
        method: "POST",
        headers: { ...auth(token), "content-type": "application/json" },
        body: JSON.stringify(body),
      });

    // someone else cannot claim, the invited user can
    const stranger = await userWithToken("stranger");
    expect((await post("/v1/contributor/claim", stranger.token, { token: code })).status).toBe(404);
    const claimed = await post("/v1/contributor/claim", contributor.token, { token: code });
    expect(claimed.status).toBe(200);
    expect(claimInviteResponse.parse(await claimed.json()).workspace_id).toBe(ws);

    const me = meResponse.parse(
      await (await app.request("/v1/me", { headers: auth(contributor.token) })).json(),
    );
    expect(me.memberships).toEqual([]);
    expect(me.contributions.map((c) => c.workspace_id)).toEqual([ws]);

    // exactly their own connection, and no workspace data
    const list = contributorConnectionsResponse.parse(
      await (
        await app.request(`/v1/workspaces/${ws}/contributor/connections`, {
          headers: auth(contributor.token),
        })
      ).json(),
    );
    expect(list.items.map((i) => i.account_label)).toEqual(["contrib-box"]);
    for (const path of ["attention", "clients", "decisions"]) {
      const res = await app.request(`/v1/workspaces/${ws}/${path}`, {
        headers: auth(contributor.token),
      });
      expect(res.status).toBe(403);
    }

    // disconnect their own, not the owner's
    const own = list.items[0]?.id ?? "";
    const ownerBox = await admin
      .from("connection")
      .select("id")
      .eq("workspace_id", ws)
      .eq("account_label", "owner-box")
      .single();
    const del = (id: string) =>
      app.request(`/v1/workspaces/${ws}/contributor/connections/${id}`, {
        method: "DELETE",
        headers: auth(contributor.token),
      });
    expect((await del(ownerBox.data?.id ?? "")).status).toBe(404);
    expect((await del(own)).status).toBe(204);
    const after = await admin
      .from("connection")
      .select("status, token_ciphertext")
      .eq("id", own)
      .single();
    expect(after.data).toEqual({ status: "revoked", token_ciphertext: null });

    // revoking the contributor removes access
    await ownerDb.rpc("revoke_contributor", { p_contributor_id: contributorId });
    const gone = meResponse.parse(
      await (await app.request("/v1/me", { headers: auth(contributor.token) })).json(),
    );
    expect(gone.contributions).toEqual([]);
  });
});

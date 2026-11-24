/** Caller mailbox isolation and body column privileges against local Supabase (CI). */
import { randomUUID } from "node:crypto";
import { vaultAckResponse, vaultRecordsResponse } from "@firra/contracts";
import type { Database } from "@firra/db-types";
import { createClient } from "@supabase/supabase-js";
import { expect, it } from "vitest";
import {
  admin,
  appFor,
  PUBLISHABLE_KEY,
  sqlFixture,
  SUPABASE_URL,
  userWithToken,
  workspace,
} from "./read-helpers.js";

it("delivers only each user's mail, audits empty polls, re-delivers changed text and denies direct bodies", async () => {
  const users = await Promise.all([
    userWithToken("vault-owner"),
    userWithToken("vault-admin"),
    userWithToken("vault-contributor"),
  ]);
  const [owner, other, contributor] = users;
  const ws = await workspace();
  const foreignWs = await workspace();
  const ids = users.map(() => randomUUID());
  const contributorId = randomUUID();
  try {
    sqlFixture(`
      insert into public.membership (workspace_id, user_id, role) values
        ('${ws}', '${owner.id}', 'owner'), ('${ws}', '${other.id}', 'admin');
      insert into public.contributor (id, workspace_id, email, status, user_id)
        values ('${contributorId}', '${ws}', '${contributor.email}', 'active', '${contributor.id}');
      do $$ declare client uuid; conn uuid; begin
        insert into public.client (workspace_id, name) values ('${ws}', 'Vault fixture') returning id into client;
        ${users
          .map(
            (user, index) => `
          insert into public.connection (workspace_id, provider, mode, account_label, external_account_id,
                                        owner_user_id, owner_contributor_id)
            values ('${ws}', 'gmail', 'user', 'fixture', '${randomUUID()}',
              ${index === 2 ? "null" : `'${user.id}'`}, ${index === 2 ? `'${contributorId}'` : "null"})
            returning id into conn;
          insert into public.source_record (id, workspace_id, connection_id, provider, external_id, kind,
            client_id, policy_at_ingest, occurred_at, direction, word_count, body_clean, content_hash,
            vault_user_id, created_at)
            values ('${ids[index]}', '${ws}', conn, 'gmail', '${randomUUID()}', 'email', client, 'full',
              now(), 'in', 3, 'synthetic mailbox ${index}', decode('0102', 'hex'), '${user.id}',
              '2026-10-01T12:00:00.123456Z');`,
          )
          .join("\n")}
        insert into public.client (workspace_id, name) values ('${foreignWs}', 'Foreign fixture') returning id into client;
        insert into public.connection (workspace_id, provider, mode, account_label, external_account_id, owner_user_id)
          values ('${foreignWs}', 'gmail', 'user', 'foreign fixture', '${randomUUID()}', '${other.id}')
          returning id into conn;
        insert into public.source_record (workspace_id, connection_id, provider, external_id, kind, client_id,
          policy_at_ingest, occurred_at, direction, body_clean, vault_user_id)
          values ('${foreignWs}', conn, 'gmail', '${randomUUID()}', 'email', client, 'full', now(),
                  'in', 'synthetic foreign mailbox', '${other.id}');
      end $$;
    `);
    const app = appFor();
    const auth = (token: string) => ({ Authorization: `Bearer ${token}` });
    for (const [index, user] of users.entries()) {
      const response = await app.request("/v1/vault/records", { headers: auth(user.token) });
      expect(response.status).toBe(200);
      const body = vaultRecordsResponse.parse(await response.json());
      expect(body.items.filter((item) => item.workspace_id === ws).map((item) => item.id)).toEqual([
        ids[index],
      ]);
      if (index !== 1) expect(body.items).toHaveLength(1);
      const callerDb = createClient<Database>(SUPABASE_URL, PUBLISHABLE_KEY, {
        auth: { persistSession: false },
        global: { headers: auth(user.token) },
      });
      for (const column of ["body_clean", "raw_object_uri"]) {
        const direct = await callerDb
          .from("source_record")
          .select(column)
          .eq("id", ids[index] ?? "");
        expect(direct.error?.code).toBe("42501");
        expect(direct.data).toBeNull();
      }
    }
    const ownId = ids[0];
    const foreignId = ids[2];
    if (!ownId || !foreignId) throw new Error("missing fixture records");
    const ack = (recordIds: string[]) =>
      app.request("/v1/vault/ack", {
        method: "POST",
        headers: { ...auth(owner.token), "content-type": "application/json" },
        body: JSON.stringify({ ids: recordIds }),
      });
    expect(vaultAckResponse.parse(await (await ack([foreignId, randomUUID()])).json())).toEqual({
      acknowledged: 0,
    });
    expect(vaultAckResponse.parse(await (await ack([ownId, foreignId])).json())).toEqual({
      acknowledged: 1,
    });
    const empty = await app.request("/v1/vault/records", { headers: auth(owner.token) });
    expect(empty.status).toBe(200);
    expect(await empty.json()).toEqual({ items: [], next_cursor: null });
    const audits = await admin
      .from("audit_log")
      .select("action, metadata")
      .eq("workspace_id", ws)
      .eq("actor_id", owner.id);
    expect(audits.error).toBeNull();
    expect(audits.data).toEqual(
      expect.arrayContaining([
        { action: "read.vault_records", metadata: { count: 1 } },
        { action: "read.vault_records", metadata: { count: 0 } },
        { action: "vault.acked", metadata: { count: 0 } },
        { action: "vault.acked", metadata: { count: 1 } },
      ]),
    );
    // Simulate the persisted result of T67's separately integration-tested changed-body upsert.
    sqlFixture(`update public.source_record set body_clean = 'synthetic changed text',
      content_hash = decode('0203', 'hex'), vault_delivered_at = null where id = '${ownId}';`);
    const changed = vaultRecordsResponse.parse(
      await (
        await app.request("/v1/vault/records", {
          headers: auth(owner.token),
        })
      ).json(),
    );
    expect(changed.items[0]?.body).toBe("synthetic changed text");
    const contributorRows = vaultRecordsResponse.parse(
      await (
        await app.request("/v1/vault/records", {
          headers: auth(contributor.token),
        })
      ).json(),
    );
    expect(contributorRows.items.map((item) => item.id)).toEqual([foreignId]);
  } finally {
    await admin.from("workspace").delete().in("id", [ws, foreignWs]);
    for (const user of users) await admin.auth.admin.deleteUser(user.id);
  }
});

it("keeps only full clients in member or active contributor workspaces, auditing empty results", async () => {
  const owner = await userWithToken("keep-owner");
  const contributor = await userWithToken("keep-contributor");
  const ws = await workspace();
  const foreignWs = await workspace();
  const full = randomUUID(),
    metadata = randomUUID(),
    excluded = randomUUID(),
    foreign = randomUUID();
  const contributorId = randomUUID();
  try {
    sqlFixture(`
      insert into public.membership (workspace_id, user_id, role) values ('${ws}', '${owner.id}', 'owner');
      insert into public.contributor (id, workspace_id, email, status, user_id)
        values ('${contributorId}', '${ws}', '${contributor.email}', 'active', '${contributor.id}');
      insert into public.client (id, workspace_id, name, policy) values
        ('${full}', '${ws}', 'Full fixture', 'full'),
        ('${metadata}', '${ws}', 'Metadata fixture', 'metadata_only'),
        ('${excluded}', '${ws}', 'Excluded fixture', 'excluded'),
        ('${foreign}', '${foreignWs}', 'Foreign fixture', 'full');
    `);
    const app = appFor();
    const request = (token: string, ids: string[]) =>
      app.request("/v1/vault/keep", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "content-type": "application/json" },
        body: JSON.stringify({ client_ids: ids }),
      });
    for (const user of [owner, contributor]) {
      const response = await request(user.token, [
        full,
        full,
        metadata,
        excluded,
        foreign,
        randomUUID(),
      ]);
      expect(response.status).toBe(200);
      expect(response.headers.get("cache-control")).toBe("no-store");
      expect(await response.json()).toEqual({ keep: [full] });
      expect(await (await request(user.token, [metadata, foreign])).json()).toEqual({ keep: [] });
      const audits = await admin
        .from("audit_log")
        .select("metadata")
        .eq("workspace_id", ws)
        .eq("actor_id", user.id)
        .eq("action", "read.vault_keep");
      expect(audits.error).toBeNull();
      expect(audits.data).toEqual(
        expect.arrayContaining([{ metadata: { count: 1 } }, { metadata: { count: 0 } }]),
      );
    }
    sqlFixture(`update public.contributor set status = 'revoked' where id = '${contributorId}';`);
    expect((await request(contributor.token, [full])).status).toBe(500);
  } finally {
    await admin.from("workspace").delete().in("id", [ws, foreignWs]);
    await admin.auth.admin.deleteUser(owner.id);
    await admin.auth.admin.deleteUser(contributor.id);
  }
});

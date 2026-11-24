/** Push routing lookups against local Supabase (service role, as in production). */
import { randomUUID } from "node:crypto";
import type { Database } from "@firra/db-types";
import { createClient } from "@supabase/supabase-js";
import { describe, expect, it } from "vitest";
import { SupabasePushStore } from "../../src/webhooks/push.js";

const SUPABASE_URL = process.env.SUPABASE_URL ?? "http://127.0.0.1:54321";
const SECRET_KEY = process.env.SUPABASE_SECRET_KEY ?? "";

describe("SupabasePushStore (local Supabase)", () => {
  it("routes Gmail pushes to active connections and reads the stored clientState hash", async () => {
    const admin = createClient<Database>(SUPABASE_URL, SECRET_KEY, {
      auth: { persistSession: false },
    });
    const address = `push-${randomUUID().slice(0, 8)}@northbeam.agency`;
    const workspaces: string[] = [];
    for (const name of ["Push A", "Push B"]) {
      const { data } = await admin.from("workspace").insert({ name }).select("id").single();
      if (!data) throw new Error("no workspace");
      workspaces.push(data.id);
    }
    const [a = "", b = ""] = workspaces;
    const connection = (workspaceId: string, provider: "gmail" | "outlook", status = "active") =>
      admin
        .from("connection")
        .insert({
          workspace_id: workspaceId,
          provider,
          mode: "user",
          account_label: address,
          external_account_id: randomUUID(),
          status: status as "active" | "revoked",
          cursor: { push: { client_state_sha256: "ab".repeat(32) } },
        })
        .select("id")
        .single()
        .then(({ data }) => data?.id ?? "");
    const gmailA = await connection(a, "gmail");
    const gmailB = await connection(b, "gmail");
    await connection(b, "gmail", "revoked");
    const outlook = await connection(a, "outlook");

    const store = new SupabasePushStore(admin);
    const routed = await store.gmailConnectionsForAddress(address);
    expect(routed.map((r) => r.connectionId).sort()).toEqual([gmailA, gmailB].sort());
    expect(await store.pushConnection(a, outlook)).toEqual({
      status: "active",
      clientStateSha256: "ab".repeat(32),
    });
    // Scoped by workspace: another workspace's id finds nothing.
    expect(await store.pushConnection(b, outlook)).toBeNull();

    await admin.from("workspace").delete().in("id", workspaces);
  });
});

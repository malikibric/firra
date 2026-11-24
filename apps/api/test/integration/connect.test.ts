/**
 * Connect flow against local Supabase: state, connection, encrypted token and workspace key are
 * stored correctly, and the Python worker can decrypt what the api encrypted.
 */
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { resolve } from "node:path";
import type { Database } from "@firra/db-types";
import { createClient } from "@supabase/supabase-js";
import { describe, expect, it } from "vitest";
import { GMAIL_SCOPE } from "../../src/connect/google.js";
import { ConnectService } from "../../src/connect/service.js";
import { SupabaseConnectStore } from "../../src/connect/store.js";
import { LocalKek } from "../../src/crypto/kek.js";
import { Keyring } from "../../src/crypto/keyring.js";
import { createLogger } from "../../src/lib/logger.js";
import { SupabaseRepository } from "../../src/lib/repository.js";
import { TaskService } from "../../src/tasks/enqueue.js";
import type { TaskQueue } from "../../src/tasks/queue.js";

const SUPABASE_URL = process.env.SUPABASE_URL ?? "http://127.0.0.1:54321";
const SECRET_KEY = process.env.SUPABASE_SECRET_KEY ?? "";
const DB_URL =
  process.env.FIRRA_DATABASE_URL ?? "postgresql://postgres:postgres@127.0.0.1:54322/postgres";
const KEK_HEX = "cd".repeat(32);
const CLIENT_ID = "cid.apps.googleusercontent.com";

const fakeGoogle = (() => {
  const part = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64url");
  return (() =>
    Promise.resolve(
      Response.json({
        refresh_token: "refresh-from-google",
        id_token: `${part({})}.${part({ iss: "https://accounts.google.com", aud: CLIENT_ID, sub: `sub-${randomUUID()}`, email: "ana@northbeam.agency", email_verified: true })}.x`,
        scope: `openid email ${GMAIL_SCOPE}`,
      }),
    )) as typeof fetch;
})();

describe("connect flow (local Supabase)", () => {
  it("stores an encrypted token the worker can decrypt and queues the first sync", async () => {
    const admin = createClient<Database>(SUPABASE_URL, SECRET_KEY, {
      auth: { persistSession: false },
    });
    const { data: ws } = await admin
      .from("workspace")
      .insert({ name: "Connect IT" })
      .select("id")
      .single();
    if (!ws) throw new Error("no workspace");
    const email = `owner-${randomUUID()}@northbeam.agency`;
    const { data: user } = await admin.auth.admin.createUser({ email, email_confirm: true });
    const userId = user.user?.id ?? "";

    const store = new SupabaseConnectStore(admin);
    const enqueued: string[] = [];
    const queue: TaskQueue = {
      enqueue: (_q, _id, envelope) => {
        enqueued.push(envelope.type);
        return Promise.resolve();
      },
    };
    const service = new ConnectService({
      store,
      keyring: new Keyring(new LocalKek(Buffer.from(KEK_HEX, "hex")), store),
      tasks: new TaskService(new SupabaseRepository(admin), queue),
      google: {
        clientId: CLIENT_ID,
        clientSecret: "s",
        redirectUri: "http://127.0.0.1:8080/oauth/google/callback",
      },
      logger: createLogger(() => undefined),
      fetchImpl: fakeGoogle,
      backfillMonths: 2,
    });

    const state =
      new URL(await service.startForMember(ws.id, userId, "/en/w/x/connections")).searchParams.get(
        "state",
      ) ?? "";
    const result = await service.handleGoogleCallback({ code: "code", state });
    expect(result.ok).toBe(true);
    if (!result.ok) return;

    const { data: connection } = await admin
      .from("connection")
      .select("provider, mode, account_label, owner_user_id, token_ciphertext")
      .eq("id", result.connectionId)
      .single();
    expect(connection).toMatchObject({
      provider: "gmail",
      mode: "user",
      account_label: "ana@northbeam.agency",
      owner_user_id: userId,
    });
    expect(connection?.token_ciphertext).toMatch(/^v1\./);
    expect(enqueued).toEqual(["sync.incremental", "sync.backfill_chunk", "sync.backfill_chunk"]);
    const { count } = await admin
      .from("job")
      .select("id", { count: "exact", head: true })
      .eq("connection_id", result.connectionId);
    expect(count).toBe(3);
    const { count: pending } = await admin
      .from("oauth_state")
      .select("id", { count: "exact", head: true })
      .eq("workspace_id", ws.id);
    expect(pending).toBe(0);

    // The Python worker decrypts the api's ciphertext with the stored, wrapped workspace key.
    const script = [
      "import os, sys",
      "from uuid import UUID",
      "from firra.crypto.kek import LocalKek",
      "from firra.crypto.keyring import Keyring",
      "from firra.db.keys import PgWorkspaceKeyStore",
      "from firra.db.pool import create_pool",
      "from firra.db.types import WorkspaceId",
      "from tests.conftest import local_settings",
      "pool = create_pool(local_settings(database_url=os.environ['DB']))",
      "pool.open()",
      "ring = Keyring(kek=LocalKek(bytes.fromhex(os.environ['KEK'])), store=PgWorkspaceKeyStore(pool))",
      "print(ring.decrypt_token(WorkspaceId(UUID(sys.argv[1])), UUID(sys.argv[2]), sys.argv[3]))",
      "pool.close()",
    ].join("\n");
    const decrypted = execFileSync(
      "uv",
      [
        "run",
        "python",
        "-c",
        script,
        ws.id,
        result.connectionId,
        connection?.token_ciphertext ?? "",
      ],
      {
        cwd: resolve(import.meta.dirname, "../../../../services/worker"),
        env: { ...process.env, DB: DB_URL, KEK: KEK_HEX },
      },
    )
      .toString()
      .trim();
    expect(JSON.parse(decrypted)).toEqual({ refresh_token: "refresh-from-google" });

    await admin.from("workspace").delete().eq("id", ws.id);
    await admin.auth.admin.deleteUser(userId);
  }, 60_000);
});

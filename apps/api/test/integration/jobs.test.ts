/** Job repository against local Supabase: a failed job is re-armed once and keeps its progress. */
import { randomUUID } from "node:crypto";
import type { Database } from "@firra/db-types";
import { createClient } from "@supabase/supabase-js";
import { describe, expect, it } from "vitest";
import { SupabaseRepository } from "../../src/lib/repository.js";

const SUPABASE_URL = process.env.SUPABASE_URL ?? "http://127.0.0.1:54321";
const SECRET_KEY = process.env.SUPABASE_SECRET_KEY ?? "";

describe("SupabaseRepository.retryFailedJob (local Supabase)", () => {
  it("re-queues a failed job with fresh attempts and its resume point", async () => {
    const admin = createClient<Database>(SUPABASE_URL, SECRET_KEY, {
      auth: { persistSession: false },
    });
    const { data: ws } = await admin
      .from("workspace")
      .insert({ name: "Jobs IT" })
      .select("id")
      .single();
    if (!ws) throw new Error("no workspace");
    try {
      const repo = new SupabaseRepository(admin);
      const job = await repo.upsertJob({
        workspaceId: ws.id,
        type: "diagnostics.ping",
        idempotencyKey: `k-${randomUUID()}`,
        payload: {},
      });
      const { error } = await admin
        .from("job")
        .update({
          status: "failed",
          attempts: 5,
          error_class: "provider",
          finished_at: new Date().toISOString(),
          progress: { page_token: "p3" },
        })
        .eq("id", job.id);
      expect(error).toBeNull();

      const retried = await repo.retryFailedJob(ws.id, job.id);
      expect(retried).toMatchObject({
        id: job.id,
        status: "queued",
        attempts: 0,
        errorClass: null,
        finishedAt: null,
        progress: { page_token: "p3" },
      });
      expect(await repo.retryFailedJob(ws.id, job.id)).toBeNull();
      expect(await repo.retryFailedJob(randomUUID(), job.id)).toBeNull();
    } finally {
      await admin.from("workspace").delete().eq("id", ws.id);
    }
  });
});

import type { JobStatus, MemberRole } from "@firra/contracts";
import type { Database } from "@firra/db-types";
import type { SupabaseClient } from "@supabase/supabase-js";

export interface JobRecord {
  id: string;
  workspaceId: string;
  type: string;
  status: JobStatus;
  idempotencyKey: string;
  attempts: number;
  progress: Record<string, unknown>;
  errorClass: string | null;
  createdAt: string;
  finishedAt: string | null;
}

export interface NewJob {
  workspaceId: string;
  type: string;
  idempotencyKey: string;
  payload: Record<string, unknown>;
  connectionId?: string;
}

export interface AuditEntry {
  workspaceId: string;
  /** `mcp` actors are personal access tokens: `actorId` is then the token id. */
  actorType?: "user" | "mcp";
  actorId: string;
  action: string;
  targetType?: string;
  targetId?: string;
  ip: string | null;
  userAgent: string | null;
  metadata: Record<string, string | number | boolean>;
}

/** Service-role data access. Every method is scoped by workspace id. */
export interface ApiRepository {
  getMembershipRole(workspaceId: string, userId: string): Promise<MemberRole | null>;
  workspaceExists(workspaceId: string): Promise<boolean>;
  /** Inserts the job or returns the existing one with the same idempotency key. */
  upsertJob(job: NewJob): Promise<JobRecord>;
  getJob(workspaceId: string, jobId: string): Promise<JobRecord | null>;
  /** Puts a failed job back in the queue with fresh attempts; its progress (resume point) stays. */
  retryFailedJob(workspaceId: string, jobId: string): Promise<JobRecord | null>;
  /** Appends a user action to `audit_log`; throws when it cannot be written (reads fail closed). */
  writeAudit(entry: AuditEntry): Promise<void>;
}

type JobRow = Database["public"]["Tables"]["job"]["Row"];

const JOB_COLUMNS =
  "id, workspace_id, type, status, idempotency_key, attempts, progress, error_class, created_at, finished_at";

function toJob(
  row: Pick<
    JobRow,
    | "id"
    | "workspace_id"
    | "type"
    | "status"
    | "idempotency_key"
    | "attempts"
    | "progress"
    | "error_class"
    | "created_at"
    | "finished_at"
  >,
): JobRecord {
  return {
    id: row.id,
    workspaceId: row.workspace_id,
    type: row.type,
    status: row.status,
    idempotencyKey: row.idempotency_key,
    attempts: row.attempts,
    progress: row.progress as Record<string, unknown>,
    errorClass: row.error_class,
    createdAt: row.created_at,
    finishedAt: row.finished_at,
  };
}

export class SupabaseRepository implements ApiRepository {
  constructor(private readonly db: SupabaseClient<Database>) {}

  async getMembershipRole(workspaceId: string, userId: string): Promise<MemberRole | null> {
    const { data, error } = await this.db
      .from("membership")
      .select("role")
      .eq("workspace_id", workspaceId)
      .eq("user_id", userId)
      .maybeSingle();
    if (error) throw new Error(`membership lookup failed: ${error.code}`);
    return data?.role ?? null;
  }

  async workspaceExists(workspaceId: string): Promise<boolean> {
    const { data, error } = await this.db
      .from("workspace")
      .select("id")
      .eq("id", workspaceId)
      .maybeSingle();
    if (error) throw new Error(`workspace lookup failed: ${error.code}`);
    return data !== null;
  }

  async upsertJob(job: NewJob): Promise<JobRecord> {
    const { error } = await this.db.from("job").upsert(
      {
        workspace_id: job.workspaceId,
        type: job.type,
        idempotency_key: job.idempotencyKey,
        payload: job.payload as Database["public"]["Tables"]["job"]["Insert"]["payload"],
        connection_id: job.connectionId ?? null,
      },
      { onConflict: "workspace_id,idempotency_key", ignoreDuplicates: true },
    );
    if (error) throw new Error(`job insert failed: ${error.code}`);

    const { data, error: selectError } = await this.db
      .from("job")
      .select(JOB_COLUMNS)
      .eq("workspace_id", job.workspaceId)
      .eq("idempotency_key", job.idempotencyKey)
      .single();
    if (selectError) throw new Error(`job select failed: ${selectError.code}`);
    return toJob(data);
  }

  async getJob(workspaceId: string, jobId: string): Promise<JobRecord | null> {
    const { data, error } = await this.db
      .from("job")
      .select(JOB_COLUMNS)
      .eq("workspace_id", workspaceId)
      .eq("id", jobId)
      .maybeSingle();
    if (error) throw new Error(`job select failed: ${error.code}`);
    return data ? toJob(data) : null;
  }

  async writeAudit(entry: AuditEntry): Promise<void> {
    const { error } = await this.db.from("audit_log").insert({
      workspace_id: entry.workspaceId,
      actor_type: entry.actorType ?? "user",
      actor_id: entry.actorId,
      action: entry.action,
      target_type: entry.targetType ?? null,
      target_id: entry.targetId ?? null,
      ip: entry.ip,
      user_agent: entry.userAgent?.slice(0, 512) ?? null,
      metadata: entry.metadata,
    });
    if (error) throw new Error(`audit write failed: ${error.code}`);
  }

  async retryFailedJob(workspaceId: string, jobId: string): Promise<JobRecord | null> {
    const { data, error } = await this.db
      .from("job")
      .update({ status: "queued", attempts: 0, error_class: null, finished_at: null })
      .eq("workspace_id", workspaceId)
      .eq("id", jobId)
      .eq("status", "failed")
      .select(JOB_COLUMNS)
      .maybeSingle();
    if (error) throw new Error(`job retry failed: ${error.code}`);
    return data ? toJob(data) : null;
  }
}

import { z } from "zod";

const csv = z
  .string()
  .transform((value) =>
    value
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean),
  )
  .pipe(z.array(z.email()));

// Unset and empty ("KEY=") both mean "not configured".
const optional = <T extends z.ZodType>(schema: T) =>
  z.preprocess((value) => (value === "" ? undefined : value), schema.optional());

export const envSchema = z
  .object({
    FIRRA_ENV: z.enum(["local", "test", "staging", "prod"]),
    PORT: z.coerce.number().int().positive().default(8080),
    RELEASE: z.string().default("dev"),
    SUPABASE_URL: z.url(),
    SUPABASE_SECRET_KEY: z.string().min(20),
    // Public key: `/v1` reads run as the caller through RLS. Unset = data routes answer 503.
    SUPABASE_PUBLISHABLE_KEY: optional(z.string().min(20)),
    WORKER_URL: z.url(),
    TASK_QUEUE_MODE: z.enum(["cloud_tasks", "local"]),
    GCP_PROJECT_ID: optional(z.string().min(1)),
    GCP_REGION: z.string().default("europe-west3"),
    TASKS_INVOKER_SA_EMAIL: optional(z.email()),
    LOCAL_TASK_SECRET: optional(z.string().min(32)),
    // Audience and allowed callers for Google-signed ID tokens on /internal/* routes.
    API_URL: z.url(),
    INTERNAL_CALLER_SA_EMAILS: csv.default([]),
    RATE_LIMIT_PER_MINUTE: z.coerce.number().int().positive().default(120),
    USER_RATE_LIMIT_PER_MINUTE: z.coerce.number().int().positive().default(300),
    // Remote MCP: per personal access token, and the browser origins allowed to call it.
    MCP_RATE_LIMIT_PER_MINUTE: z.coerce.number().int().positive().default(120),
    MCP_ALLOWED_ORIGINS: z
      .string()
      .default("")
      .transform((value) =>
        value
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
      )
      .pipe(z.array(z.url())),
    SENTRY_DSN: optional(z.url()),
    // Best-effort transactional invite mail; the API always returns the invite code.
    RESEND_API_KEY: optional(z.string().min(1)),
    EMAIL_FROM: optional(z.string().min(1)),
    // Optional legacy web origin; without it OAuth callbacks return to the Mac or a static page.
    WEB_URL: optional(z.url().transform((url) => url.replace(/\/$/, ""))),
    GOOGLE_CLIENT_ID: optional(z.string().min(1)),
    GOOGLE_CLIENT_SECRET: optional(z.string().min(1)),
    // Microsoft Entra multi-tenant app (Outlook). Tenant: organizations | common | <tenant id>.
    MICROSOFT_CLIENT_ID: optional(z.string().min(1)),
    MICROSOFT_CLIENT_SECRET: optional(z.string().min(1)),
    MICROSOFT_TENANT: z
      .string()
      .regex(/^[A-Za-z0-9.-]+$/)
      .default("organizations"),
    // Numeric OAuth client id of the worker service account (Google domain-wide delegation).
    WORKER_SA_CLIENT_ID: optional(z.string().regex(/^\d{10,30}$/)),
    // Mail export uploads: the raw bucket (cloud), or a local directory shared with the worker's
    // FIRRA_LOCAL_RAW_DIR (local/test only).
    RAW_BUCKET: optional(z.string().regex(/^[a-z0-9._-]{3,63}$/)),
    LOCAL_RAW_DIR: optional(z.string().min(1)),
    // Service account whose Google-signed OIDC tokens Pub/Sub attaches to Gmail push messages.
    GMAIL_PUSH_SA_EMAIL: optional(z.email()),
    // Token-encryption KEK: Cloud KMS (staging/prod) or a static key (local/test only).
    KMS_MODE: z.enum(["cloud", "local"]).default("cloud"),
    KMS_KEY_NAME: optional(z.string().min(1)),
    LOCAL_KEK_HEX: optional(z.string().regex(/^[0-9a-f]{64}$/)),
  })
  .superRefine((env, ctx) => {
    if (
      env.TASK_QUEUE_MODE === "cloud_tasks" &&
      (!env.GCP_PROJECT_ID || !env.TASKS_INVOKER_SA_EMAIL)
    ) {
      ctx.addIssue({
        code: "custom",
        message: "cloud_tasks mode needs GCP_PROJECT_ID and TASKS_INVOKER_SA_EMAIL",
      });
    }
    if (env.LOCAL_RAW_DIR && env.FIRRA_ENV !== "local" && env.FIRRA_ENV !== "test") {
      ctx.addIssue({ code: "custom", message: "LOCAL_RAW_DIR is only allowed in local/test" });
    }
    if (env.KMS_MODE === "cloud" && !env.KMS_KEY_NAME) {
      ctx.addIssue({ code: "custom", message: "cloud kms mode needs KMS_KEY_NAME" });
    }
    if (
      env.KMS_MODE === "local" &&
      (!env.LOCAL_KEK_HEX || (env.FIRRA_ENV !== "local" && env.FIRRA_ENV !== "test"))
    ) {
      ctx.addIssue({
        code: "custom",
        message: "local kms mode needs LOCAL_KEK_HEX and is only allowed in local/test",
      });
    }
    if (env.TASK_QUEUE_MODE === "local") {
      if (env.FIRRA_ENV !== "local" && env.FIRRA_ENV !== "test") {
        ctx.addIssue({ code: "custom", message: "local task queue is only allowed in local/test" });
      }
      if (!env.LOCAL_TASK_SECRET) {
        ctx.addIssue({ code: "custom", message: "local task queue needs LOCAL_TASK_SECRET" });
      }
    }
  });

export type Env = z.infer<typeof envSchema>;

export function loadEnv(source: NodeJS.ProcessEnv = process.env): Env {
  const parsed = envSchema.safeParse(source);
  if (!parsed.success) {
    // Report variable names only; values may be secrets.
    const problems = parsed.error.issues.map(
      (issue) => `${issue.path.join(".") || "env"}: ${issue.message}`,
    );
    throw new Error(`Invalid environment:\n${problems.join("\n")}`);
  }
  return parsed.data;
}

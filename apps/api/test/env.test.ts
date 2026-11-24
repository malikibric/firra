import { describe, expect, it } from "vitest";
import { loadEnv } from "../src/env.js";

const base = {
  FIRRA_ENV: "local",
  SUPABASE_URL: "http://127.0.0.1:54321",
  SUPABASE_SECRET_KEY: "test-only-placeholder-key",
  WORKER_URL: "http://127.0.0.1:8081",
  API_URL: "http://127.0.0.1:8080",
  TASK_QUEUE_MODE: "local",
  LOCAL_TASK_SECRET: "x".repeat(32),
  WEB_URL: "http://localhost:3000/",
  KMS_MODE: "local",
  LOCAL_KEK_HEX: "ab".repeat(32),
};

describe("loadEnv", () => {
  it("parses a valid local environment", () => {
    const env = loadEnv({
      ...base,
      INTERNAL_CALLER_SA_EMAILS: "a@x.iam.gserviceaccount.com, b@y.com",
    });
    expect(env.PORT).toBe(8080);
    expect(env.INTERNAL_CALLER_SA_EMAILS).toEqual(["a@x.iam.gserviceaccount.com", "b@y.com"]);
  });

  it("refuses the local dispatcher outside local/test", () => {
    expect(() => loadEnv({ ...base, FIRRA_ENV: "staging" })).toThrow(/only allowed in local\/test/);
  });

  it("requires GCP settings for Cloud Tasks", () => {
    expect(() =>
      loadEnv({ ...base, FIRRA_ENV: "staging", TASK_QUEUE_MODE: "cloud_tasks" }),
    ).toThrow(/GCP_PROJECT_ID/);
  });

  it("never echoes secret values in errors", () => {
    const secret = "sb_secret_do_not_print_me";
    try {
      loadEnv({ ...base, SUPABASE_SECRET_KEY: secret, WORKER_URL: "not a url" });
      expect.unreachable();
    } catch (error) {
      expect(String(error)).toContain("WORKER_URL");
      expect(String(error)).not.toContain(secret);
    }
  });
});

describe("empty values", () => {
  it("treat KEY= as unset", () => {
    const env = loadEnv({
      ...base,
      SENTRY_DSN: "",
      GCP_PROJECT_ID: "",
      TASKS_INVOKER_SA_EMAIL: "",
    });
    expect(env.SENTRY_DSN).toBeUndefined();
    expect(env.GCP_PROJECT_ID).toBeUndefined();
  });
});

describe("kms", () => {
  it("normalizes WEB_URL and refuses the local KEK outside local/test", () => {
    expect(loadEnv(base).WEB_URL).toBe("http://localhost:3000");
    expect(() =>
      loadEnv({
        ...base,
        FIRRA_ENV: "staging",
        TASK_QUEUE_MODE: "cloud_tasks",
        GCP_PROJECT_ID: "p",
        TASKS_INVOKER_SA_EMAIL: "i@p.iam.gserviceaccount.com",
      }),
    ).toThrow(/local kms mode/);
    expect(() => loadEnv({ ...base, KMS_MODE: "cloud" })).toThrow(/KMS_KEY_NAME/);
  });
});

it("boots without WEB_URL or with an empty legacy value, denying browser MCP origins by default", () => {
  const withoutWeb: NodeJS.ProcessEnv = { ...base };
  delete withoutWeb.WEB_URL;
  expect(loadEnv(withoutWeb).WEB_URL).toBeUndefined();
  expect(loadEnv({ ...base, WEB_URL: "" }).WEB_URL).toBeUndefined();
  expect(loadEnv(withoutWeb).MCP_ALLOWED_ORIGINS).toEqual([]);
});

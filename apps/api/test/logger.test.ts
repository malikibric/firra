import { describe, expect, it } from "vitest";
import { createLogger, REDACTED, sanitizeValue } from "../src/lib/logger.js";

describe("logger", () => {
  it("redacts free text unless the key is allowlisted", () => {
    expect(sanitizeValue("subject", "Renewal for ACME")).toBe(REDACTED);
    expect(sanitizeValue("email", "ceo@acme.com")).toBe(REDACTED);
    expect(sanitizeValue("task_type", "diagnostics.ping")).toBe("diagnostics.ping");
    expect(sanitizeValue("job_id", "8e7b9d2a-3c4f-4b1a-a9e6-0d1c2b3a4f5e")).toBe(
      "8e7b9d2a-3c4f-4b1a-a9e6-0d1c2b3a4f5e",
    );
    expect(sanitizeValue("count", 3)).toBe(3);
    expect(sanitizeValue("obj", { body: "x" })).toBe(REDACTED);
  });

  it("writes Cloud Logging JSON and logs only the error class", () => {
    const lines: string[] = [];
    createLogger((line) => lines.push(line)).error("task.failed", {
      err: new TypeError("contains client@example.com"),
    });
    const entry = JSON.parse(lines[0] ?? "{}") as Record<string, unknown>;
    expect(entry).toMatchObject({
      severity: "ERROR",
      message: "task.failed",
      error_class: "TypeError",
    });
    expect(lines[0]).not.toContain("client@example.com");
  });
});

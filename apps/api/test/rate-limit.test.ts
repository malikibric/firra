import { randomUUID } from "node:crypto";
import { describe, expect, it } from "vitest";
import { testApp } from "./helpers.js";

describe("rate limiting", () => {
  it("limits per client IP on /v1 and sets Retry-After", async () => {
    const { app } = testApp({ rateLimitPerMinute: 2 });
    const hit = (ip: string) =>
      app.request(`/v1/workspaces/${randomUUID()}/diagnostics/ping`, {
        method: "POST",
        headers: { "x-forwarded-for": `spoofed-${String(Math.random())}, ${ip}` },
      });
    expect((await hit("1.1.1.1")).status).toBe(401);
    expect((await hit("1.1.1.1")).status).toBe(401);
    const limited = await hit("1.1.1.1");
    expect(limited.status).toBe(429);
    expect(Number(limited.headers.get("retry-after"))).toBeGreaterThan(0);
    expect((await hit("2.2.2.2")).status).toBe(401);
  });

  it("ignores client-supplied X-Forwarded-For entries", async () => {
    const { app } = testApp({ rateLimitPerMinute: 2 });
    const statuses: number[] = [];
    for (let i = 0; i < 3; i += 1) {
      const response = await app.request(`/v1/workspaces/${randomUUID()}/diagnostics/ping`, {
        method: "POST",
        headers: { "x-forwarded-for": `10.9.${String(i)}.7, 3.3.3.3` },
      });
      statuses.push(response.status);
    }
    expect(statuses).toEqual([401, 401, 429]);
  });

  it("does not limit health checks", async () => {
    const { app } = testApp({ rateLimitPerMinute: 1 });
    for (let i = 0; i < 3; i += 1) expect((await app.request("/health")).status).toBe(200);
  });
});

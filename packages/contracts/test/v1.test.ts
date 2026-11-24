import { describe, expect, it } from "vitest";
import { apiError, attentionResponse, openApiDocument } from "../src/index.js";

const item = {
  client_id: "22222222-2222-4222-8222-222222222222",
  name: "Redfern",
  status: "active",
  policy: "full",
  as_of: "2026-10-01",
  risk_state: "high",
  previous_risk_state: null,
  state_since: "2026-09-25",
  expansion_state: "none",
  strength: "moderate",
  play: null,
  days_to_notice: null,
  drivers: [],
};

describe("v1 contracts", () => {
  it("accepts a valid attention response and rejects excluded clients and long driver lists", () => {
    const ok = { generated_at: "2026-10-02T09:00:00.000Z", items: [item] };
    expect(attentionResponse.safeParse(ok).success).toBe(true);
    expect(
      attentionResponse.safeParse({ ...ok, items: [{ ...item, policy: "excluded" }] }).success,
    ).toBe(false);
    const driver = {
      type: "x",
      family: null,
      severity: "low",
      basis: "metadata",
      occurred_at: null,
      primary: false,
      evidence_count: 0,
    };
    expect(
      attentionResponse.safeParse({
        ...ok,
        items: [{ ...item, drivers: [driver, driver, driver] }],
      }).success,
    ).toBe(false);
  });

  it("uses only the documented error codes", () => {
    expect(apiError.safeParse({ error: "nope", message: "x", request_id: "r" }).success).toBe(
      false,
    );
    expect(apiError.safeParse({ error: "not_found", message: "x", request_id: "r" }).success).toBe(
      true,
    );
  });

  it("generates an OpenAPI document whose references all resolve", () => {
    const doc = openApiDocument("1.2.3") as {
      openapi: string;
      info: { version: string };
      components: { schemas: Record<string, unknown> };
    };
    expect(doc.openapi).toBe("3.1.0");
    expect(doc.info.version).toBe("1.2.3");
    const refs = [...JSON.stringify(doc).matchAll(/#\/components\/schemas\/(\w+)/g)].map(
      (m) => m[1] ?? "",
    );
    expect(refs.length).toBeGreaterThan(0);
    for (const name of refs) expect(Object.keys(doc.components.schemas)).toContain(name);
    expect(JSON.stringify(doc)).not.toContain("$schema");
  });
});

import { describe, expect, it } from "vitest";
import {
  clientsResponse,
  clientDetailResponse,
  clientDriver,
  metadataInputs,
  openApiDocument,
} from "../src/v1/index.js";
const basics = {
  id: "22222222-2222-4222-8222-222222222222",
  name: "Synthetic client",
  status: "active",
  policy: "full",
  mrr: null,
  currency: null,
  renewal_date: null,
  notice_period_days: null,
};
const generated_at = "2026-10-01T12:00:00Z";
describe("Client contracts", () => {
  it("accepts unassessed clients, enforces list bounds and rejects excluded policy", () => {
    const item = { ...basics, assessment: null };
    clientsResponse.parse({ generated_at, items: [item] });
    expect(clientsResponse.safeParse({ generated_at, items: Array(501).fill(item) }).success).toBe(
      false,
    );
    expect(
      clientsResponse.safeParse({ generated_at, items: [{ ...item, policy: "excluded" }] }).success,
    ).toBe(false);
  });
  it("accepts empty detail arrays and bounds state history", () => {
    const client = {
      ...basics,
      assessment: null,
      state_history: [],
      stakeholders: [],
      commitments: [],
      goals: [],
      needs: [],
      outcomes: [],
    };
    clientDetailResponse.parse({ generated_at, client });
    expect(
      clientDetailResponse.safeParse({
        generated_at,
        client: {
          ...client,
          state_history: Array(21).fill({ as_of: "2026-10-01", from: "stable", to: "watch" }),
        },
      }).success,
    ).toBe(false);
  });
  it("strips evidence inputs and allows only typed metadata inputs", () => {
    const driver = {
      type: "delivery_complaint",
      family: null,
      severity: "high",
      occurred_at: null,
      primary: true,
      evidence_ids: [],
    };
    expect(
      clientDriver.parse({ ...driver, basis: "evidence", inputs: { quote: "PRIVATE BODY" } }),
    ).not.toHaveProperty("inputs");
    expect(metadataInputs.parse({ days_to_notice: 10, quote: "PRIVATE BODY" })).toEqual({
      days_to_notice: 10,
    });
    expect(metadataInputs.safeParse({ last_message_at: "PRIVATE BODY" }).success).toBe(false);
  });
  it("documents both paths, filters, response schemas and standard errors", () => {
    const doc = openApiDocument("test") as {
      paths: Record<
        string,
        {
          get: {
            responses: Record<string, unknown>;
            parameters: { name: string; schema: unknown }[];
          };
        }
      >;
    };
    for (const path of [
      "/workspaces/{workspaceId}/clients",
      "/workspaces/{workspaceId}/clients/{clientId}",
    ]) {
      for (const status of ["200", "401", "403", "422", "429", "500", "503"])
        expect(doc.paths[path]?.get.responses).toHaveProperty(status);
    }
    expect(doc.paths["/workspaces/{workspaceId}/clients/{clientId}"]?.get.responses).toHaveProperty(
      "404",
    );
    expect(doc.paths["/workspaces/{workspaceId}/clients"]?.get.parameters).toContainEqual({
      name: "status",
      in: "query",
      required: false,
      schema: { type: "string", enum: ["active", "churned", "all"], default: "active" },
    });
  });
});

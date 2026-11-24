import { describe, expect, it } from "vitest";
import { feedbackRequest, outcomeRequest } from "./decisions.js";
import { openApiDocument } from "./openapi.js";

describe("Closed loop contracts", () => {
  it("accepts exactly the persisted verdict and action enums with bounded notes", () => {
    for (const verdict of ["useful", "already_known", "wrong", "not_now"])
      expect(
        feedbackRequest.safeParse({ verdict, action: "planned", note: "x".repeat(1000) }).success,
      ).toBe(true);
    expect(feedbackRequest.safeParse({ verdict: "useful", action: "sent" }).success).toBe(false);
    expect(feedbackRequest.safeParse({ verdict: "wrong", note: "x".repeat(1001) }).success).toBe(
      false,
    );
  });
  it("enforces database outcome invariants before RPC execution", () => {
    const base = { event: "churned", occurred_at: "2026-10-01" };
    expect(
      outcomeRequest.safeParse({
        ...base,
        notice_at: "2026-09-01",
        amount_change: -100,
        currency: "EUR",
      }).success,
    ).toBe(true);
    for (const extra of [
      { occurred_at: "2026-02-30" },
      { notice_at: "2026-10-02" },
      { first_known_at: "2026-10-02" },
      { amount_change: 10 },
      { currency: "EUR" },
    ])
      expect(outcomeRequest.safeParse({ ...base, ...extra }).success).toBe(false);
  });
  it("documents all four operations and JSON request and success schemas", () => {
    const doc = openApiDocument("test") as {
      paths: Record<
        string,
        Record<
          string,
          { operationId: string; requestBody?: unknown; responses: Record<string, unknown> }
        >
      >;
    };
    expect(doc.paths["/workspaces/{workspaceId}/decisions"]?.get?.operationId).toBe("getDecisions");
    for (const path of ["/decisions/{cardId}/feedback", "/clients/{clientId}/outcomes"])
      expect(doc.paths[`/workspaces/{workspaceId}${path}`]?.post?.requestBody).toBeDefined();
    expect(
      doc.paths["/workspaces/{workspaceId}/outcomes/{outcomeId}"]?.delete?.responses["204"],
    ).toBeDefined();
    expect(
      doc.paths["/workspaces/{workspaceId}/outcomes/{outcomeId}"]?.delete?.responses["200"],
    ).toBeUndefined();
  });
});

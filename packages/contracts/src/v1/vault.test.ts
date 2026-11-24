import { randomUUID } from "node:crypto";
import { describe, expect, it } from "vitest";
import { vaultAckRequest, vaultAckResponse, vaultRecordsResponse } from "./vault.js";
import { openApiDocument } from "./openapi.js";

describe("vault contracts", () => {
  it("bounds acknowledgements and validates UUIDs", () => {
    expect(vaultAckRequest.parse({ ids: [randomUUID()] }).ids).toHaveLength(1);
    for (const ids of [[], ["not-uuid"], Array.from({ length: 201 }, () => randomUUID())]) {
      expect(vaultAckRequest.safeParse({ ids }).success).toBe(false);
    }
    expect(vaultAckResponse.parse({ acknowledged: 0 })).toEqual({ acknowledged: 0 });
    expect(vaultAckResponse.safeParse({ acknowledged: -1 }).success).toBe(false);
  });
  it("defines empty polling and both user-only paths in OpenAPI", () => {
    expect(vaultRecordsResponse.parse({ items: [], next_cursor: null })).toEqual({
      items: [],
      next_cursor: null,
    });
    const doc = openApiDocument("fixture");
    expect(doc.paths).toHaveProperty("/vault/records");
    expect(doc.paths).toHaveProperty("/vault/ack");
  });
});

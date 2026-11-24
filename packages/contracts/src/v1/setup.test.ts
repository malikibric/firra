import { describe, expect, it } from "vitest";
import {
  createWorkspaceRequest,
  createClientRequest,
  saveServiceRequest,
  inviteContributorRequest,
  requestReplayRequest,
  connectionsResponse,
  clientDomainsRequest,
  adminInviteResponse,
  contributorInviteResponse,
} from "./setup.js";
import { openApiDocument } from "./openapi.js";

describe("setup contracts", () => {
  it("preserves creation defaults and setup's excluded policy without accepting invalid fields", () => {
    expect(createWorkspaceRequest.parse({ name: " Workspace " })).toEqual({
      name: "Workspace",
      timezone: "UTC",
      default_locale: "en",
    });
    expect(createClientRequest.parse({ name: " Client " })).toEqual({
      name: "Client",
      policy: "full",
      domains: [],
      confirm_exclusion: false,
    });
    expect(
      createClientRequest.safeParse({ name: "Client", policy: "excluded", confirm_exclusion: true })
        .success,
    ).toBe(true);
    expect(createWorkspaceRequest.safeParse({ name: "", default_locale: "es" }).success).toBe(
      false,
    );
    expect(createClientRequest.safeParse({ name: "x", policy: "unknown" }).success).toBe(false);
  });
  it("accepts the service and contributor forms, rejects malformed domains, keywords, dates and emails", () => {
    expect(saveServiceRequest.parse({ name: "Service" })).toEqual({
      name: "Service",
      description: "",
      keywords: [],
    });
    expect(
      saveServiceRequest.safeParse({
        name: "Svc",
        keywords: Array.from({ length: 21 }, () => "word"),
      }).success,
    ).toBe(false);
    expect(inviteContributorRequest.safeParse({ email: "not-an-email" }).success).toBe(false);
    expect(clientDomainsRequest.safeParse({ domains: ["https://evil.example/path"] }).success).toBe(
      false,
    );
    expect(
      requestReplayRequest.safeParse({ window_start: "2026-02-30", window_end: "2026-04-01" })
        .success,
    ).toBe(false);
    expect(
      requestReplayRequest.safeParse({
        window_start: "2026-01-01",
        window_end: "2026-02-01",
        step_days: 32,
      }).success,
    ).toBe(false);
    expect(adminInviteResponse.safeParse({ id: "bad", token: "bad" }).success).toBe(false);
    expect(connectionsResponse.safeParse({ items: [{ id: "bad" }] }).success).toBe(false);
  });
  it("describes all setup requests/responses without requiring Content-Type on DELETE", () => {
    const doc = openApiDocument("test") as {
      paths: Record<
        string,
        Record<string, { parameters?: { name: string }[]; requestBody?: unknown }>
      >;
    };
    expect(doc.paths["/workspaces/{workspaceId}/connections/{connectionId}"]?.delete).toBeDefined();
    expect(doc.paths["/workspaces/{workspaceId}/services"]?.post).toBeDefined();
    for (const path of Object.values(doc.paths))
      if (path.delete) {
        expect(path.delete.requestBody).toBeUndefined();
        expect(
          path.delete.parameters?.some((parameter) => parameter.name === "Content-Type"),
        ).not.toBe(true);
      }
  });
});

it("invite contracts and OpenAPI expose a code without a web link", () => {
  const id = "11111111-1111-4111-8111-111111111111";
  const token = "ab".repeat(32);
  expect(
    adminInviteResponse.parse({ id, token, expires_at: "2026-10-03T00:00:00Z", emailed: false }),
  ).not.toHaveProperty("link");
  expect(contributorInviteResponse.parse({ id, token })).toEqual({ id, token });
  const doc = openApiDocument("test") as {
    components: {
      schemas: Record<string, { properties: Record<string, unknown>; required: string[] }>;
    };
  };
  for (const name of ["inviteAdminResponse", "inviteContributorResponse"]) {
    const model = doc.components.schemas[name];
    expect(model?.properties).not.toHaveProperty("link");
    expect(model?.required).not.toContain("link");
  }
});

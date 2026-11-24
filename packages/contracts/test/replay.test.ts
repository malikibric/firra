import { expect, it } from "vitest";
import {
  replayNarrative,
  replayDriver,
  replayClientResponse,
  replayRunsResponse,
  openApiDocument,
} from "../src/index.js";
const empty = {
  status: "active",
  end_date: null,
  end_kind: null,
  detection_at: null,
  detection_state: null,
  confirmed_at: null,
  lead_days: null,
  confirmed_lead_days: null,
  max_state: null,
  weeks_elevated: 0,
  transitions: [],
  visible_at_detection: [],
  detection_timing: [],
  detection_play: null,
  caveats: [],
  last_as_of: null,
  last_state: null,
  unconfirmed_at_end: false,
  events: [],
};
it("accepts an empty computed narrative and rejects a partial narrative", () => {
  expect(replayNarrative.parse(empty)).toEqual(empty);
  expect(replayNarrative.safeParse({ status: "active" }).success).toBe(false);
  expect(replayClientResponse.safeParse({ narrative: empty }).success).toBe(false);
  expect(
    replayRunsResponse.safeParse({ generated_at: "2026-10-01T12:00:00Z", items: [] }).success,
  ).toBe(true);
});
it("strips rendered wording and evidence inputs; validates typed metadata and ids", () => {
  const base = {
    type: "reduced_engagement",
    family: "engagement",
    severity: "medium",
    primary: true,
    occurred_at: "2026-09-30",
    evidence_ids: [],
    label: "PRIVATE LABEL",
    detail: "PRIVATE DETAIL",
  };
  expect(
    replayDriver.parse({
      ...base,
      basis: "metadata",
      inputs: { messages_30: 0, text: "PRIVATE BODY" },
    }),
  ).toEqual({
    type: "reduced_engagement",
    family: "engagement",
    severity: "medium",
    primary: true,
    occurred_at: "2026-09-30",
    evidence_ids: [],
    basis: "metadata",
    inputs: { messages_30: 0 },
  });
  expect(
    replayDriver.safeParse({ ...base, basis: "metadata", inputs: { messages_30: "body" } }).success,
  ).toBe(false);
  expect(
    replayDriver.safeParse({ ...base, basis: "evidence", evidence_ids: ["foreign-text"] }).success,
  ).toBe(false);
  expect(
    replayDriver.parse({ ...base, basis: "evidence", inputs: { text: "PRIVATE" } }),
  ).not.toHaveProperty("inputs");
});
it("publishes both replay endpoints with the shared failure responses", () => {
  const doc = openApiDocument("test") as {
    paths: Record<string, { get: { responses: Record<string, unknown> } }>;
  };
  for (const path of [
    "/workspaces/{workspaceId}/replay/runs",
    "/workspaces/{workspaceId}/replay/runs/{runId}/clients/{clientId}",
  ]) {
    expect(Object.keys(doc.paths[path]?.get.responses ?? {})).toEqual(
      expect.arrayContaining(["200", "401", "403", "422", "429", "500", "503"]),
    );
  }
});

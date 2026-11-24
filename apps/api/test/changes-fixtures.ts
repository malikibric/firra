import type { ChangeAssessmentRow, ChangeSignalEventRow } from "../src/lib/changes.js";
import type { ClientRow } from "../src/lib/attention.js";
import { CLIENT } from "./client-fixtures.js";
export const NOW = new Date("2026-10-03T12:00:00Z");
export const SINCE = "2026-09-26";
export const clients: ClientRow[] = [
  CLIENT,
  {
    id: "44444444-4444-4444-8444-444444444444",
    name: "Alpha",
    status: "active",
    policy: "metadata_only",
  },
];
export function assessment(over: Partial<ChangeAssessmentRow> = {}): ChangeAssessmentRow {
  return {
    client_id: CLIENT.id,
    as_of: SINCE,
    risk_state: "watch",
    previous_risk_state: "stable",
    expansion_state: "none",
    ...over,
  };
}
export function event(over: Partial<ChangeSignalEventRow> = {}): ChangeSignalEventRow {
  return {
    id: "55555555-5555-4555-8555-555555555555",
    client_id: CLIENT.id,
    signal_id: "66666666-6666-4666-8666-666666666666",
    as_of: `${SINCE}T00:00:00Z`,
    created_at: `${SINCE}T00:00:00Z`,
    change: "opened",
    severity: "low",
    signal: {
      type: "delivery_complaint",
      family: "delivery",
      basis: "evidence",
      signal_evidence: [{ evidence_id: "77777777-7777-4777-8777-777777777777" }],
    },
    ...over,
  };
}

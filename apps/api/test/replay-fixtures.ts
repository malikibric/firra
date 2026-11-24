import type { ReplayNarrative, ReplayRun } from "@firra/contracts";
export const RUN_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
export const EVIDENCE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
export const NARRATIVE: ReplayNarrative = {
  status: "active",
  end_date: null,
  end_kind: null,
  detection_at: "2026-09-23",
  detection_state: "high",
  confirmed_at: "2026-09-30",
  lead_days: null,
  confirmed_lead_days: null,
  max_state: "high",
  weeks_elevated: 2,
  transitions: [],
  visible_at_detection: [
    {
      type: "delivery_complaint",
      family: "delivery",
      severity: "high",
      basis: "evidence",
      occurred_at: "2026-09-23",
      primary: true,
      evidence_ids: [EVIDENCE_ID],
    },
  ],
  detection_timing: [],
  detection_play: "fix_first",
  caveats: [],
  last_as_of: "2026-09-30",
  last_state: "high",
  unconfirmed_at_end: false,
  events: [],
};
export const RUN: ReplayRun = {
  id: RUN_ID,
  window_start: "2026-09-01",
  window_end: "2026-09-30",
  step_days: 7,
  status: "succeeded",
  finished_at: "2026-09-30T12:00:00Z",
  narrated: true,
  totals: {
    losses: 0,
    losses_detected_30d: 0,
    median_lead_days: null,
    retained: 1,
    retained_with_elevated: 1,
    false_alarm_client_weeks_per_10_clients_per_month: 2,
    flicker: 0,
  },
};

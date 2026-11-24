import type { ClientBasicsRow, ClientAssessmentRow } from "../src/lib/clients.js";
export const WS = "11111111-1111-4111-8111-111111111111";
export const CLIENT: ClientBasicsRow = {
  id: "22222222-2222-4222-8222-222222222222",
  name: "Synthetic Portfolio Client",
  status: "active",
  policy: "full",
  mrr: 4000,
  currency: "EUR",
  renewal_date: "2026-11-01",
  notice_period_days: 30,
};
export const EVIDENCE_ID = "33333333-3333-4333-8333-333333333333";
export const ASSESSMENT: ClientAssessmentRow = {
  as_of: "2026-10-01",
  risk_state: "high",
  previous_risk_state: "watch",
  state_since: "2026-10-01",
  expansion_state: "none",
  strength: "moderate",
  play: "fix_first",
  caveats: [{ code: "low_data", detail: "Few client messages.", evidence_ids: [EVIDENCE_ID] }],
  drivers: [
    {
      type: "delivery_complaint",
      family: "delivery",
      severity: "high",
      basis: "evidence",
      occurred_at: "2026-09-30T08:00:00+00:00",
      primary: true,
      evidence_ids: [EVIDENCE_ID],
      inputs: { quote: "PRIVATE BODY" },
    },
    {
      type: "renewal_window",
      family: "commercial",
      severity: "medium",
      basis: "metadata",
      occurred_at: "2026-10-01T00:00:00+00:00",
      primary: false,
      evidence_ids: [EVIDENCE_ID],
      inputs: { days_to_notice: 1, renewal_date: "2026-11-01", quote: "PRIVATE BODY" },
    },
  ],
};
export const PARTS = {
  cardContent: {
    unknowns: [{ code: "budget_unknown", detail: "Budget not stated." }],
    private_extra: "PRIVATE BODY",
  },
  stakeholders: [
    {
      display_name: "Synthetic Person",
      role_title: null,
      is_decision_maker: false,
      is_champion: true,
      last_seen_at: "2026-09-30T08:00:00+00:00",
    },
  ],
  commitments: [
    {
      owner_side: "firm",
      text: "Synthetic open commitment",
      due_date: "2026-10-05",
      status: "open",
      commitment_evidence: [{ evidence_id: EVIDENCE_ID, created_at: "2026-09-30T08:00:00+00:00" }],
    },
  ],
  goals: [
    {
      text: "Synthetic goal",
      metric: "10",
      stated_at: "2026-09-30T08:00:00+00:00",
      client_goal_evidence: [{ evidence_id: EVIDENCE_ID, created_at: "2026-09-30T08:00:00+00:00" }],
    },
  ],
  needs: [
    {
      topic: "Synthetic need",
      latent_need_evidence: [{ evidence_id: EVIDENCE_ID, created_at: "2026-09-30T08:00:00+00:00" }],
      mention_count: 3,
      thread_count: 2,
      first_seen_at: "2026-09-01T08:00:00+00:00",
      last_seen_at: "2026-09-30T08:00:00+00:00",
    },
  ],
  outcomes: [
    {
      event: "renewed",
      occurred_at: "2026-09-30",
      notice_at: null,
      first_known_at: null,
      amount_change: null,
      currency: null,
    },
  ],
};

export function required<T>(value: T): NonNullable<T> {
  if (value === null || value === undefined) throw new Error("Missing synthetic test value");
  return value;
}

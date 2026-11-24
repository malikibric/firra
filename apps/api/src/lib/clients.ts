import {
  clientAssessment,
  clientAssessmentSummary,
  clientBasics,
  clientDetail,
  clientDriver,
  clientListItem,
  clientStateChange,
  metadataInputs,
} from "@firra/contracts";
import type { ClientDetail, ClientListItem, ClientStateChange } from "@firra/contracts";
import type { Database } from "@firra/db-types";
import { z } from "zod";
import { instant } from "./read-page.js";

type Tables = Database["public"]["Tables"];
export type ClientBasicsRow = Pick<
  Tables["client"]["Row"],
  "id" | "name" | "status" | "policy" | "mrr" | "currency" | "renewal_date" | "notice_period_days"
>;
export type ClientAssessmentRow = Pick<
  Tables["account_assessment"]["Row"],
  | "as_of"
  | "risk_state"
  | "previous_risk_state"
  | "state_since"
  | "expansion_state"
  | "strength"
  | "play"
  | "drivers"
  | "caveats"
>;
export type HistoryRow = Pick<ClientAssessmentRow, "as_of" | "risk_state" | "previous_risk_state">;

export function buildClientListItem(
  client: ClientBasicsRow,
  assessment: Pick<
    ClientAssessmentRow,
    "risk_state" | "state_since" | "expansion_state" | "play" | "as_of"
  > | null,
): ClientListItem {
  return clientListItem.parse({
    ...clientBasics.parse(client),
    assessment: assessment ? clientAssessmentSummary.parse(assessment) : null,
  });
}

/** Consecutive unchanged assessments are not state changes. Initial states have no `from`. */
export function buildStateHistory(rows: readonly HistoryRow[]): ClientStateChange[] {
  return [...rows]
    .sort((a, b) => b.as_of.localeCompare(a.as_of))
    .filter((r) => r.previous_risk_state !== null && r.previous_risk_state !== r.risk_state)
    .slice(0, 20)
    .map((r) =>
      clientStateChange.parse({ as_of: r.as_of, from: r.previous_risk_state, to: r.risk_state }),
    );
}

const storedDriver = z.object({
  type: z.string(),
  family: z.string().nullish(),
  severity: z.enum(["low", "medium", "high"]),
  basis: z.enum(["evidence", "metadata"]),
  occurred_at: z.string().nullish(),
  primary: z.boolean().optional(),
  evidence_ids: z.array(z.uuid()).optional(),
  inputs: z.unknown().optional(),
});

export interface ClientParts {
  stakeholders: unknown[];
  commitments: unknown[];
  goals: unknown[];
  needs: unknown[];
  outcomes: unknown[];
  cardContent?: unknown;
}

/** The newest links first, bounded per claim; embedded join fields stay internal. */
function withEvidence(rows: unknown[], join: string): unknown[] {
  return rows.map((row) => {
    const fields = z.record(z.string(), z.unknown()).parse(row);
    const links = z
      .array(z.object({ evidence_id: z.uuid(), created_at: z.string() }))
      .parse(fields[join] ?? []);
    return {
      ...fields,
      evidence_ids: [...links]
        .sort(
          (a, b) =>
            instant(b.created_at).localeCompare(instant(a.created_at)) ||
            a.evidence_id.localeCompare(b.evidence_id),
        )
        .slice(0, 5)
        .map((link) => link.evidence_id),
    };
  });
}

/** Select only the contract fields; JSON blobs never pass through to the response. */
export function buildClientDetail(
  client: ClientBasicsRow,
  assessment: ClientAssessmentRow | null,
  history: readonly HistoryRow[],
  parts: ClientParts,
): ClientDetail {
  const metadataOnly = client.policy === "metadata_only";
  const drivers = assessment
    ? z
        .array(storedDriver)
        .parse(assessment.drivers)
        .filter((d) => !metadataOnly || d.basis === "metadata")
        .map((d) =>
          clientDriver.parse({
            type: d.type,
            family: d.family ?? null,
            severity: d.severity,
            basis: d.basis,
            occurred_at: d.occurred_at ?? null,
            primary: d.primary ?? false,
            evidence_ids: d.basis === "metadata" ? [] : (d.evidence_ids ?? []),
            ...(d.basis === "metadata" ? { inputs: metadataInputs.parse(d.inputs ?? {}) } : {}),
          }),
        )
    : [];
  return clientDetail.parse({
    ...clientBasics.parse(client),
    assessment: assessment ? clientAssessment.parse({ ...assessment, drivers }) : null,
    state_history: buildStateHistory(history),
    ...parts,
    unknowns:
      z.object({ unknowns: z.array(z.unknown()).optional() }).parse(parts.cardContent ?? {})
        .unknowns ?? [],
    commitments: metadataOnly ? [] : withEvidence(parts.commitments, "commitment_evidence"),
    goals: metadataOnly ? [] : withEvidence(parts.goals, "client_goal_evidence"),
    needs: metadataOnly ? [] : withEvidence(parts.needs, "latent_need_evidence"),
  });
}

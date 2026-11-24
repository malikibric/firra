import type { AttentionItem } from "@firra/contracts";
import { z } from "zod";

const RISK_RANK = { stable: 0, watch: 1, elevated: 2, high: 3 } as const;
type RiskState = keyof typeof RISK_RANK;
const SEVERITY_RANK = { low: 0, medium: 1, high: 2 } as const;
const RECENT_CHANGE_DAYS = 14;
const DAY_MS = 86_400_000;

export interface AssessmentRow {
  client_id: string;
  as_of: string;
  risk_state: RiskState;
  previous_risk_state: RiskState | null;
  state_since: string;
  expansion_state: AttentionItem["expansion_state"];
  strength: AttentionItem["strength"];
  play: string | null;
  drivers: unknown;
}

export interface ClientRow {
  id: string;
  name: string;
  status: AttentionItem["status"];
  policy: string;
}

const driver = z.object({
  type: z.string(),
  family: z.string().nullish(),
  severity: z.enum(["low", "medium", "high"]),
  basis: z.enum(["evidence", "metadata"]),
  occurred_at: z.string().nullish(),
  primary: z.boolean().optional(),
  inputs: z.record(z.string(), z.unknown()).nullish(),
  evidence_ids: z.array(z.string()).optional(),
});
const drivers = z.array(driver);

function daysToNotice(rows: z.infer<typeof drivers>): number | null {
  for (const d of rows) {
    const days = d.type === "renewal_window" ? d.inputs?.days_to_notice : undefined;
    if (typeof days === "number" && Number.isInteger(days)) return days;
  }
  return null;
}

function topDrivers(rows: z.infer<typeof drivers>): AttentionItem["drivers"] {
  const ranked = [...rows].sort(
    (a, b) =>
      Number(a.family === "expansion") - Number(b.family === "expansion") ||
      Number(b.primary ?? false) - Number(a.primary ?? false) ||
      SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity] ||
      (b.occurred_at ?? "").localeCompare(a.occurred_at ?? ""),
  );
  return ranked.slice(0, 2).map((d) => ({
    type: d.type,
    family: d.family ?? null,
    severity: d.severity,
    basis: d.basis,
    occurred_at: d.occurred_at ? d.occurred_at.slice(0, 10) : null,
    primary: d.primary ?? false,
    evidence_count: d.evidence_ids?.length ?? 0,
  }));
}

/**
 * Clients that need attention: the latest live assessment per active, visible client at
 * `elevated`/`high`, or whose state changed in the last 14 days. Most urgent first.
 */
export function buildAttention(
  assessments: readonly AssessmentRow[],
  clients: readonly ClientRow[],
  now: Date,
): AttentionItem[] {
  const byId = new Map(clients.map((c) => [c.id, c]));
  const latest = new Map<string, AssessmentRow>();
  for (const row of assessments) {
    const seen = latest.get(row.client_id);
    if ((seen?.as_of ?? "") < row.as_of) latest.set(row.client_id, row);
  }
  const since = new Date(now.getTime() - RECENT_CHANGE_DAYS * DAY_MS).toISOString().slice(0, 10);
  const items: AttentionItem[] = [];
  for (const row of latest.values()) {
    const client = byId.get(row.client_id);
    if (client?.status !== "active") continue;
    if (client.policy !== "full" && client.policy !== "metadata_only") continue;
    const changed =
      row.previous_risk_state !== null &&
      row.previous_risk_state !== row.risk_state &&
      row.state_since >= since;
    if (RISK_RANK[row.risk_state] < RISK_RANK.elevated && !changed) continue;
    const parsed = drivers.safeParse(row.drivers);
    const rows = parsed.success ? parsed.data : [];
    items.push({
      client_id: client.id,
      name: client.name,
      status: client.status,
      policy: client.policy,
      as_of: row.as_of,
      risk_state: row.risk_state,
      previous_risk_state: row.previous_risk_state,
      state_since: row.state_since,
      expansion_state: row.expansion_state,
      strength: row.strength,
      play: row.play,
      days_to_notice: daysToNotice(rows),
      drivers: topDrivers(rows),
    });
  }
  return items
    .sort(
      (a, b) =>
        RISK_RANK[b.risk_state] - RISK_RANK[a.risk_state] ||
        (a.days_to_notice ?? Infinity) - (b.days_to_notice ?? Infinity) ||
        b.state_since.localeCompare(a.state_since) ||
        a.name.localeCompare(b.name),
    )
    .slice(0, 150);
}

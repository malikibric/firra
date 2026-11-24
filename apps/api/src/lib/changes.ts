import type { ChangeItem, ChangeKind } from "@firra/contracts";
import { changeKind } from "@firra/contracts";
import { z } from "zod";
import { instant } from "./read-page.js";
import type { ClientRow } from "./attention.js";

export const changeAssessment = z.object({
  client_id: z.uuid(),
  as_of: z.iso.date(),
  risk_state: z.enum(["stable", "watch", "elevated", "high"]),
  previous_risk_state: z.enum(["stable", "watch", "elevated", "high"]).nullable(),
  expansion_state: z.enum(["none", "hypothesis", "strong"]),
});
export const changeSignal = z.object({
  type: z.string(),
  family: z.string().nullable(),
  basis: z.enum(["metadata", "evidence"]),
  signal_evidence: z.array(z.object({ evidence_id: z.uuid() })),
});
export const changeSignalEvent = z.object({
  id: z.uuid(),
  client_id: z.uuid(),
  signal_id: z.uuid(),
  as_of: z.iso.datetime({ offset: true }),
  created_at: z.iso.datetime({ offset: true }),
  change: z.enum(["opened", "reopened", "severity_changed", "resolved"]),
  severity: z.enum(["low", "medium", "high"]),
  signal: changeSignal,
});
export type ChangeAssessmentRow = z.infer<typeof changeAssessment>;
export type ChangeSignalEventRow = z.infer<typeof changeSignalEvent>;
const SEVERITY = { low: 0, medium: 1, high: 2 } as const;

/** Merge stored live transitions, including pre-window rows only as comparison baselines. */
export function buildChanges(
  assessments: readonly ChangeAssessmentRow[],
  events: readonly ChangeSignalEventRow[],
  clients: readonly ClientRow[],
  since: string,
  now: Date,
): ChangeItem[] {
  const visible = new Map(
    clients
      .filter((c) => c.status === "active" && (c.policy === "full" || c.policy === "metadata_only"))
      .map((c) => [c.id, c]),
  );
  const today = now.toISOString().slice(0, 10);
  const items: ChangeItem[] = [];
  const base = (client: ClientRow, as_of: string, kind: ChangeKind): ChangeItem => ({
    client_id: client.id,
    client_name: client.name,
    as_of,
    kind,
    from: null,
    to: null,
    signal_type: null,
    family: null,
    severity: null,
    evidence_count: 0,
  });
  const previousExpansion = new Map<string, ChangeAssessmentRow["expansion_state"]>();
  for (const row of [...assessments].sort((a, b) => a.as_of.localeCompare(b.as_of))) {
    const client = visible.get(row.client_id);
    if (!client || row.as_of > today) continue;
    const previous = previousExpansion.get(row.client_id);
    previousExpansion.set(row.client_id, row.expansion_state);
    if (row.as_of < since) continue;
    if (row.previous_risk_state !== null && row.previous_risk_state !== row.risk_state)
      items.push({
        ...base(client, row.as_of, "risk_state_changed"),
        from: row.previous_risk_state,
        to: row.risk_state,
      });
    if (previous !== undefined && previous !== row.expansion_state)
      items.push({
        ...base(client, row.as_of, "expansion_state_changed"),
        from: previous,
        to: row.expansion_state,
      });
  }
  const previousSeverity = new Map<string, ChangeSignalEventRow["severity"]>();
  for (const row of [...events].sort(
    (a, b) =>
      instant(a.as_of).localeCompare(instant(b.as_of)) ||
      instant(a.created_at).localeCompare(instant(b.created_at)) ||
      a.id.localeCompare(b.id),
  )) {
    const client = visible.get(row.client_id);
    if (!client || instant(row.as_of) > instant(now.toISOString())) continue;
    const previous = previousSeverity.get(row.signal_id);
    previousSeverity.set(row.signal_id, row.severity);
    const day = new Date(row.as_of).toISOString().slice(0, 10);
    if (day < since) continue;
    const kind =
      row.change === "opened" || row.change === "reopened"
        ? "signal_opened"
        : row.change === "resolved"
          ? "signal_resolved"
          : previous !== undefined && SEVERITY[row.severity] > SEVERITY[previous]
            ? "signal_escalated"
            : null;
    if (!kind) continue;
    items.push({
      ...base(client, day, kind),
      signal_type: row.signal.type,
      family: row.signal.family,
      severity: row.severity,
      evidence_count:
        kind === "signal_resolved" ||
        row.signal.basis === "metadata" ||
        client.policy === "metadata_only"
          ? 0
          : row.signal.signal_evidence.length,
    });
  }
  return items
    .sort(
      (a, b) =>
        b.as_of.localeCompare(a.as_of) ||
        a.client_name.localeCompare(b.client_name) ||
        changeKind.options.indexOf(a.kind) - changeKind.options.indexOf(b.kind),
    )
    .slice(0, 300);
}

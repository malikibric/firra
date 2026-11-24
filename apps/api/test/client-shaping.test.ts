import { required } from "./client-fixtures.js";
import { describe, expect, it } from "vitest";
import { buildClientDetail, buildClientListItem, buildStateHistory } from "../src/lib/clients.js";
import { ASSESSMENT, CLIENT, EVIDENCE_ID, PARTS } from "./client-fixtures.js";
describe("Client shaping", () => {
  it("handles clients without assessments and strips database-only fields", () => {
    expect(buildClientListItem(CLIENT, null).assessment).toBeNull();
    const detail = buildClientDetail(CLIENT, null, [], { ...PARTS, commitments: [] });
    expect(detail.assessment).toBeNull();
    expect(detail.state_history).toEqual([]);
    expect(detail.outcomes).toEqual(PARTS.outcomes);
  });
  it("exposes evidence ids but never evidence inputs or arbitrary JSON fields", () => {
    const result = buildClientDetail(CLIENT, ASSESSMENT, [ASSESSMENT], PARTS);
    expect(required(required(result.assessment).drivers[0]).evidence_ids).toEqual([EVIDENCE_ID]);
    expect(required(result.assessment).drivers[0]).not.toHaveProperty("inputs");
    expect(required(result.assessment).drivers[1]).toMatchObject({
      inputs: { days_to_notice: 1, renewal_date: "2026-11-01" },
      evidence_ids: [],
    });
    expect(required(result.assessment).caveats).toEqual([
      { code: "low_data", detail: "Few client messages." },
    ]);
    expect(JSON.stringify(result)).not.toContain("PRIVATE BODY");
  });
  it("empties body-derived lists and evidence drivers for metadata-only clients", () => {
    const result = buildClientDetail(
      { ...CLIENT, policy: "metadata_only" },
      ASSESSMENT,
      [ASSESSMENT],
      PARTS,
    );
    expect(result.commitments).toEqual([]);
    expect(result.goals).toEqual([]);
    expect(result.needs).toEqual([]);
    expect(required(result.assessment).drivers).toHaveLength(1);
    expect(required(result.assessment).drivers[0]).toMatchObject({
      basis: "metadata",
      evidence_ids: [],
      inputs: { days_to_notice: 1 },
    });
    expect(result.outcomes).toEqual(PARTS.outcomes);
  });
  it("returns champions, stored unknowns and newest evidence links capped at five per claim", () => {
    const links = Array.from({ length: 7 }, (_, i) => ({
      evidence_id: `33333333-3333-4333-8333-${String(i).padStart(12, "0")}`,
      created_at: `2026-09-${String(i + 1).padStart(2, "0")}T00:00:00Z`,
    }));
    const result = buildClientDetail(CLIENT, null, [], {
      ...PARTS,
      commitments: [{ ...PARTS.commitments[0], commitment_evidence: links }],
      goals: [{ ...PARTS.goals[0], client_goal_evidence: links }],
      needs: [{ ...PARTS.needs[0], latent_need_evidence: links }],
    });
    expect(result.stakeholders[0]).toMatchObject({ is_champion: true });
    expect(result.unknowns).toEqual(PARTS.cardContent.unknowns);
    const expected = links
      .slice(2)
      .reverse()
      .map((link) => link.evidence_id);
    for (const claims of [result.commitments, result.goals, result.needs]) {
      expect(claims[0]?.evidence_ids).toEqual(expected);
      expect(JSON.stringify(claims)).not.toContain("created_at");
      expect(JSON.stringify(claims)).not.toContain("_evidence");
    }
    expect(JSON.stringify(result)).not.toContain("PRIVATE BODY");
  });
  it("preserves microsecond ordering of evidence links", () => {
    const older = "33333333-3333-4333-8333-000000000001";
    const newer = "33333333-3333-4333-8333-000000000002";
    const result = buildClientDetail(CLIENT, null, [], {
      ...PARTS,
      commitments: [
        {
          ...PARTS.commitments[0],
          commitment_evidence: [
            { evidence_id: older, created_at: "2026-10-01T00:00:00.000001Z" },
            { evidence_id: newer, created_at: "2026-10-01T00:00:00.000002Z" },
          ],
        },
      ],
    });
    expect(result.commitments[0]?.evidence_ids).toEqual([newer, older]);
  });
  it("returns empty unknowns and evidence arrays when no card or links exist", () => {
    const result = buildClientDetail(CLIENT, null, [], {
      ...PARTS,
      cardContent: undefined,
      commitments: [{ ...PARTS.commitments[0], commitment_evidence: [] }],
      goals: [{ ...PARTS.goals[0], client_goal_evidence: [] }],
      needs: [{ ...PARTS.needs[0], latent_need_evidence: [] }],
    });
    expect(result.unknowns).toEqual([]);
    for (const claims of [result.commitments, result.goals, result.needs])
      expect(claims[0]?.evidence_ids).toEqual([]);
    const metadata = buildClientDetail({ ...CLIENT, policy: "metadata_only" }, null, [], {
      ...PARTS,
      cardContent: { unknowns: [{ code: "budget_unknown", detail: "budget_unknown" }] },
    });
    expect(metadata.unknowns).toEqual([{ code: "budget_unknown", detail: "budget_unknown" }]);
    expect([...metadata.commitments, ...metadata.goals, ...metadata.needs]).toEqual([]);
  });
  it("returns actual live state changes newest first, capped at twenty", () => {
    const rows = Array.from({ length: 25 }, (_, i) => ({
      ...ASSESSMENT,
      as_of: `2026-09-${String(i + 1).padStart(2, "0")}`,
    }));
    const history = buildStateHistory([
      ...rows,
      { ...ASSESSMENT, as_of: "2026-10-01", previous_risk_state: "high" },
      { ...ASSESSMENT, as_of: "2026-10-02", previous_risk_state: null },
    ]);
    expect(history).toHaveLength(20);
    expect(history[0]).toEqual({ as_of: "2026-09-25", from: "watch", to: "high" });
    expect(required(history[19]).as_of).toBe("2026-09-06");
  });
});

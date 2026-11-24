import { describe, expect, it } from "vitest";
import { buildAttention, type AssessmentRow, type ClientRow } from "../src/lib/attention.js";

const NOW = new Date("2026-10-02T09:00:00Z");

function row(client: string, over: Partial<AssessmentRow> = {}): AssessmentRow {
  return {
    client_id: client,
    as_of: "2026-10-01",
    risk_state: "stable",
    previous_risk_state: null,
    state_since: "2026-06-01",
    expansion_state: "none",
    strength: "weak",
    play: "monitor",
    drivers: [],
    ...over,
  };
}

const clients: ClientRow[] = [
  { id: "a", name: "Alpha", status: "active", policy: "full" },
  { id: "b", name: "Bravo", status: "active", policy: "metadata_only" },
  { id: "c", name: "Charlie", status: "active", policy: "full" },
  { id: "d", name: "Delta", status: "churned", policy: "full" },
  { id: "e", name: "Echo", status: "active", policy: "full" },
];

describe("buildAttention", () => {
  it("keeps elevated/high and recent changes, newest assessment per client, most urgent first", () => {
    const items = buildAttention(
      [
        row("a", { as_of: "2026-09-20", risk_state: "high" }), // older, superseded
        row("a", { risk_state: "elevated", state_since: "2026-09-25" }),
        row("b", { risk_state: "high", state_since: "2026-09-01" }),
        row("c", { risk_state: "stable", previous_risk_state: "watch", state_since: "2026-09-28" }),
        row("d", { risk_state: "high" }), // churned: out
        row("e", { risk_state: "watch", previous_risk_state: "stable", state_since: "2026-07-01" }),
        row("zzz", { risk_state: "high" }), // not visible (excluded or unknown): out
      ],
      clients,
      NOW,
    );
    expect(items.map((i) => [i.name, i.risk_state])).toEqual([
      ["Bravo", "high"],
      ["Alpha", "elevated"],
      ["Charlie", "stable"],
    ]);
    expect(items[1]?.as_of).toBe("2026-10-01");
  });

  it("orders equal risk by days to notice, then by newest state", () => {
    const renewal = (days: number) => [
      {
        type: "renewal_window",
        family: "timing",
        severity: "medium",
        basis: "metadata",
        occurred_at: "2026-10-01T00:00:00+00:00",
        inputs: { days_to_notice: days },
      },
    ];
    const items = buildAttention(
      [
        row("a", { risk_state: "high", drivers: renewal(40) }),
        row("b", { risk_state: "high", drivers: renewal(12) }),
        row("c", { risk_state: "high", state_since: "2026-09-30" }),
      ],
      clients,
      NOW,
    );
    expect(items.map((i) => i.name)).toEqual(["Bravo", "Alpha", "Charlie"]);
    expect(items[0]?.days_to_notice).toBe(12);
    expect(items[2]?.days_to_notice).toBeNull();
  });

  it("returns at most two drivers, risk drivers first, without free text", () => {
    const d = (type: string, severity: string, family = "delivery", primary = false) => ({
      type,
      family,
      severity,
      basis: "evidence",
      occurred_at: "2026-09-30T10:00:00+00:00",
      primary,
      evidence_ids: ["e1", "e2"],
    });
    const [item] = buildAttention(
      [
        row("a", {
          risk_state: "high",
          drivers: [
            d("expansion_need", "high", "expansion", true),
            d("repeated_complaint", "medium"),
            d("delivery_complaint", "high", "delivery", true),
            d("late_commitment", "low"),
          ],
        }),
      ],
      clients,
      NOW,
    );
    expect(item?.drivers.map((x) => x.type)).toEqual(["delivery_complaint", "repeated_complaint"]);
    expect(item?.drivers[0]).toEqual({
      type: "delivery_complaint",
      family: "delivery",
      severity: "high",
      basis: "evidence",
      occurred_at: "2026-09-30",
      primary: true,
      evidence_count: 2,
    });
  });

  it("tolerates malformed driver json and never returns more than 150 clients", () => {
    const [item] = buildAttention(
      [row("a", { risk_state: "high", drivers: "oops" })],
      clients,
      NOW,
    );
    expect(item?.drivers).toEqual([]);
    const many = Array.from({ length: 200 }, (_, i) => ({
      id: `c${i}`,
      name: `C${String(i).padStart(3, "0")}`,
      status: "active" as const,
      policy: "full",
    }));
    const rows = many.map((c) => row(c.id, { risk_state: "high" }));
    expect(buildAttention(rows, many, NOW)).toHaveLength(150);
  });
});

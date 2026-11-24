import { changesResponse } from "@firra/contracts";
import { describe, expect, it } from "vitest";
import { buildChanges } from "../src/lib/changes.js";
import { CLIENT } from "./client-fixtures.js";
import { NOW, SINCE, assessment, clients, event } from "./changes-fixtures.js";

describe("buildChanges", () => {
  it("merges all five kinds with inclusive boundaries and pre-window baselines", () => {
    const items = buildChanges(
      [
        assessment({ as_of: "2026-09-25", previous_risk_state: null }),
        assessment({ expansion_state: "hypothesis" }),
      ],
      [
        event({ as_of: "2026-09-25T12:00:00Z" }),
        event({ change: "severity_changed", severity: "high" }),
        event({ change: "resolved", as_of: "2026-09-27T00:00:00Z" }),
        event({ signal_id: crypto.randomUUID(), change: "reopened" }),
      ],
      clients,
      SINCE,
      NOW,
    );
    expect(items.map((i) => i.kind)).toEqual([
      "signal_resolved",
      "risk_state_changed",
      "expansion_state_changed",
      "signal_opened",
      "signal_escalated",
    ]);
    expect(items.find((i) => i.kind === "expansion_state_changed")).toMatchObject({
      from: "none",
      to: "hypothesis",
    });
    expect(items.find((i) => i.kind === "signal_escalated")).toMatchObject({
      severity: "high",
      evidence_count: 1,
    });
    expect(items[0]).toMatchObject({ from: null, to: null, evidence_count: 0 });
    expect(
      changesResponse.safeParse({ generated_at: NOW.toISOString(), since: SINCE, items }).success,
    ).toBe(true);
  });
  it("omits unchanged and initial assessments, lower/equal severity, and unsupported escalations", () => {
    expect(
      buildChanges(
        [
          assessment({ previous_risk_state: "watch" }),
          assessment({ as_of: "2026-09-27", previous_risk_state: null }),
        ],
        [
          event({ as_of: "2026-09-25T00:00:00Z", severity: "high" }),
          event({ change: "severity_changed", severity: "medium" }),
          event({ change: "severity_changed", severity: "medium", as_of: "2026-09-27T00:00:00Z" }),
          event({ signal_id: crypto.randomUUID(), change: "severity_changed", severity: "high" }),
        ],
        clients,
        SINCE,
        NOW,
      ),
    ).toEqual([]);
  });
  it("uses a lowering as the baseline for a later escalation, and compares signals separately", () => {
    const items = buildChanges(
      [],
      [
        event({ as_of: "2026-09-25T00:00:00Z", severity: "high" }),
        event({ change: "severity_changed", severity: "low" }),
        event({ change: "severity_changed", severity: "medium", as_of: "2026-09-27T00:00:00Z" }),
      ].reverse(),
      clients,
      SINCE,
      NOW,
    );
    expect(items.map((i) => [i.kind, i.severity])).toEqual([["signal_escalated", "medium"]]);
  });
  it("interleaves two clients by date, name, then the fixed kind order", () => {
    const alpha = clients[1];
    if (!alpha) throw new Error("missing fixture");
    const items = buildChanges(
      [assessment(), assessment({ client_id: alpha.id }), assessment({ as_of: "2026-09-27" })],
      [event(), event({ client_id: alpha.id })],
      clients,
      SINCE,
      NOW,
    );
    expect(items.map((i) => [i.as_of, i.client_name, i.kind])).toEqual([
      ["2026-09-27", CLIENT.name, "risk_state_changed"],
      [SINCE, "Alpha", "risk_state_changed"],
      [SINCE, "Alpha", "signal_opened"],
      [SINCE, CLIENT.name, "risk_state_changed"],
      [SINCE, CLIENT.name, "signal_opened"],
    ]);
    expect(
      items.find((i) => i.client_name === "Alpha" && i.kind === "signal_opened")?.evidence_count,
    ).toBe(0);
  });
  it("caps at 300 after sorting, keeps newest, and excludes future and hidden clients", () => {
    const events = Array.from({ length: 305 }, (_, i) =>
      event({
        id: crypto.randomUUID(),
        signal_id: crypto.randomUUID(),
        as_of: i < 300 ? "2026-10-01T00:00:00Z" : `${SINCE}T00:00:00Z`,
      }),
    );
    events.push(
      event({ as_of: "2026-10-03T13:00:00Z" }),
      event({ client_id: crypto.randomUUID() }),
    );
    const items = buildChanges([assessment({ as_of: "2026-10-04" })], events, clients, SINCE, NOW);
    expect(items).toHaveLength(300);
    expect(items.every((i) => i.as_of === "2026-10-01")).toBe(true);
    expect(
      buildChanges([assessment()], [event()], [{ ...CLIENT, policy: "excluded" }], SINCE, NOW),
    ).toEqual([]);
    expect(
      buildChanges([assessment()], [event()], [{ ...CLIENT, status: "churned" }], SINCE, NOW),
    ).toEqual([]);
  });
  it("preserves microsecond event ordering when timestamps otherwise tie", () => {
    const items = buildChanges(
      [],
      [
        event({
          id: "11111111-1111-4111-8111-111111111111",
          change: "severity_changed",
          severity: "high",
          created_at: `${SINCE}T00:00:00.000002Z`,
        }),
        event({
          id: "ffffffff-ffff-4fff-8fff-ffffffffffff",
          created_at: `${SINCE}T00:00:00.000001Z`,
        }),
      ],
      clients,
      SINCE,
      NOW,
    );
    expect(items.map((i) => i.kind)).toEqual(["signal_opened", "signal_escalated"]);
  });
  it("never returns text or evidence counts for metadata signals", () => {
    const source = event();
    const [item] = buildChanges(
      [],
      [{ ...source, signal: { ...source.signal, basis: "metadata" } }],
      clients,
      SINCE,
      NOW,
    );
    expect(item?.evidence_count).toBe(0);
    expect(item).not.toHaveProperty("text");
    expect(item).not.toHaveProperty("quote");
  });
});

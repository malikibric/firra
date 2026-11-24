/**
 * T14 requirements 1, 2, 3 and 7: coverage guard, workspace isolation, roles and id/paging
 * handling. Expectations come from `CLAUDE.md` §3/§6 and the roadmap C/F evals, not from the code.
 */
import { randomUUID } from "node:crypto";
import { apiError } from "@firra/contracts";
import { describe, expect, it } from "vitest";
import { mcpPayload, mcpRequest } from "../mcp-tool-helpers.js";
import { RUN_ID } from "../replay-fixtures.js";
import {
  A_SECRET,
  A_TOKENS,
  CA,
  CB,
  CARD_A,
  CARD_B,
  EA,
  EB,
  NAME_CA,
  NAME_CB,
  OUTCOME_A,
  WA,
  WB,
  request,
  world,
} from "./fixtures.js";

const CLIENT_DATA_READS = [
  "/attention",
  "/changes",
  "/decisions",
  "/clients",
  "/clients/:clientId",
  "/clients/:clientId/timeline",
  "/clients/:clientId/evidence",
  "/evidence/:evidenceId",
  "/meetings/upcoming",
  "/replay/runs",
  "/replay/runs/:runId/clients/:clientId",
] as const;

const SETUP_READS = [
  "/routines",
  "/services",
  "/members",
  "/contributors",
  "/connections",
  "/invites",
  "/audit",
  "/setup",
  "/setup/clients",
  "/clients/:clientId/domains",
] as const;

/** Mutations reachable under `/v1/workspaces/:workspaceId`; all are covered by the generic guards. */
const SETUP_WRITES: [string, string][] = [
  ["PUT", "/routines/:kind"],
  ["POST", "/clients"],
  ["PATCH", "/clients/:clientId"],
  ["PUT", "/clients/:clientId/domains"],
  ["PUT", "/clients/:clientId/policy"],
  ["POST", "/services"],
  ["PUT", "/services/:serviceId"],
  ["DELETE", "/services/:serviceId"],
  ["POST", "/invites"],
  ["DELETE", "/invites/:inviteId"],
  ["DELETE", "/members/:userId"],
  ["POST", "/contributors"],
  ["DELETE", "/contributors/:contributorId"],
  ["DELETE", "/connections/:connectionId"],
  ["POST", "/replay/runs"],
];
const DECISION_MUTATIONS: [string, string][] = [
  ["POST", "/decisions/:cardId/feedback"],
  ["POST", "/clients/:clientId/outcomes"],
  ["DELETE", "/outcomes/:outcomeId"],
];

/** Every matrix surface (reads and writes) as `[method, path template]`. */
const SURFACES: [string, string][] = [
  ...CLIENT_DATA_READS.map((path) => ["GET", path] as [string, string]),
  ...SETUP_READS.map((path) => ["GET", path] as [string, string]),
  ...SETUP_WRITES,
  ...DECISION_MUTATIONS,
];

/** Concrete ids for a path template. The guard refuses before any lookup, so values are arbitrary. */
const PLACEHOLDERS: Record<string, string> = {
  kind: "morning_scan",
  clientId: CA,
  evidenceId: EA,
  runId: RUN_ID,
  cardId: CARD_A,
  outcomeId: OUTCOME_A,
};
function concrete(workspaceId: string, template: string): string {
  const path = template.replace(/:(\w+)/g, (_, key: string) => PLACEHOLDERS[key] ?? randomUUID());
  return `/v1/workspaces/${workspaceId}${path}`;
}

/**
 * Surfaces the brief puts out of scope or that have their own suite. Listed explicitly so a new
 * `/v1/workspaces/:workspaceId` route fails the coverage test until it is exercised here.
 */
const OUT_OF_SCOPE: RegExp[] = [
  /\/org\//, // domain-wide delegation / OAuth (out of scope)
  /\/uploads/, // import uploads (connect.test.ts)
  /\/contributor\/connections/, // contributor-own sources (contributor.test.ts)
  /\/mcp\/tokens/, // token administration (mcp-tokens.test.ts)
  /\/diagnostics\/ping$/, // diagnostics (diagnostics.test.ts)
  /\/jobs\//, // job status (diagnostics.test.ts)
  /\/connections\/:provider\/start$/, // OAuth connect handoff (connect.test.ts)
];

const EXPECTED_TOOLS = [
  "explain_client_change",
  "get_churn_replay",
  "get_client_state",
  "get_client_timeline",
  "get_client_unknowns",
  "get_clients_needing_attention",
  "get_decisions",
  "get_open_commitments",
  "get_recent_changes",
  "get_stakeholders",
  "list_clients",
  "search_evidence",
].sort();

describe("requirement 1: coverage guard", () => {
  it("every registered /v1/workspaces route is exercised or explicitly out of scope", () => {
    const { app } = world();
    const registered = new Set(
      app.routes
        .filter((route) => route.path.startsWith("/v1/workspaces/"))
        .map((route) => `${route.method.toUpperCase()} ${route.path}`),
    );
    const covered = new Set(
      [
        ...CLIENT_DATA_READS.map((path) => `GET /v1/workspaces/:workspaceId${path}`),
        ...SETUP_READS.map((path) => `GET /v1/workspaces/:workspaceId${path}`),
        ...[...SETUP_WRITES, ...DECISION_MUTATIONS].map(
          ([method, path]) => `${method} /v1/workspaces/:workspaceId${path}`,
        ),
      ].map((entry) => entry),
    );
    const uncovered = [...registered].filter(
      (entry) =>
        !covered.has(entry) &&
        !OUT_OF_SCOPE.some((pattern) => pattern.test(entry.split(" ")[1] ?? "")),
    );
    expect(uncovered).toEqual([]);
    // A covered entry that no longer exists means the matrix went stale.
    expect([...covered].filter((entry) => !registered.has(entry))).toEqual([]);
  });

  it("the MCP tool list matches the matrix", async () => {
    const t = world();
    const noAuth = await request(t.app, "POST", "/mcp", null);
    expect(noAuth.status).toBe(401);
    const payload = await mcpPayload(
      await t.app.request("/mcp", mcpRequest(A_SECRET, "tools/list")),
    );
    const tools = (payload.result?.tools ?? []).map((tool) => tool.name).sort();
    expect(tools).toEqual(EXPECTED_TOOLS);
  });
});

describe("requirement 2: workspace isolation", () => {
  const aBase = `/v1/workspaces/${WA}`;
  const bBase = `/v1/workspaces/${WB}`;

  it("every A persona is refused on every B surface, and owner B on every A surface", async () => {
    const t = world();
    // Every surface, not just one route: the guard is generic middleware and this keeps it that way.
    for (const token of A_TOKENS) {
      for (const [method, template] of SURFACES) {
        const res = await request(t.app, method, concrete(WB, template), token);
        expect(res.status, `${token} ${method} ${template}`).toBe(403);
      }
    }
    for (const [method, template] of SURFACES) {
      const res = await request(t.app, method, concrete(WA, template), "owner-b");
      expect(res.status, `${method} ${template}`).toBe(403);
    }
    // The refusal is one shape and leaks nothing from the other workspace.
    const refusal = await request(t.app, "GET", `${bBase}/attention`, "owner-a");
    const body = JSON.stringify(await refusal.json());
    expect(apiError.parse(JSON.parse(body)).error).toBe("forbidden");
    for (const leak of [WB, CB, NAME_CB]) expect(body).not.toContain(leak);
    // The guard runs before any read or write.
    expect(t.reads.attention).not.toHaveBeenCalled();
    expect(t.writes.calls).toEqual([]);
  });

  it("no A read returns an id, name or count that belongs to B", async () => {
    const t = world();
    const paths = [
      "/attention",
      "/changes",
      "/clients",
      "/decisions",
      "/meetings/upcoming",
      "/replay/runs",
    ];
    for (const path of paths) {
      const res = await request(t.app, "GET", `${aBase}${path}`, "owner-a");
      expect(res.status).toBe(200);
      const text = await res.text();
      for (const leak of [WB, CB, CARD_B, EB, NAME_CB]) expect(text).not.toContain(leak);
    }
  });

  it("a B client id under A behaves exactly like a missing id (status and body)", async () => {
    const t = world();
    const reads: [string, (id: string) => string][] = [
      ["detail", (id) => `${aBase}/clients/${id}`],
      ["timeline", (id) => `${aBase}/clients/${id}/timeline`],
      ["evidence", (id) => `${aBase}/clients/${id}/evidence`],
      ["replay", (id) => `${aBase}/replay/runs/cccccccc-cccc-4ccc-8ccc-cccccccccccc/clients/${id}`],
    ];
    for (const [, build] of reads) {
      const foreign = await request(t.app, "GET", build(CB), "owner-a");
      const missing = await request(
        t.app,
        "GET",
        build("c0000000-0000-4000-8000-0000000000fe"),
        "owner-a",
      );
      expect(foreign.status).toBe(404);
      expect(missing.status).toBe(404);
      const a = apiError.parse(await foreign.json());
      const b = apiError.parse(await missing.json());
      expect(a.error).toBe(b.error);
      expect(a.message).toBe(b.message);
      expect(a.request_id).not.toBe(b.request_id);
    }
    // Mutations: forbidden foreign targets are the same indistinguishable 404.
    for (const url of [
      `${aBase}/clients/${CB}/outcomes`,
      `${aBase}/decisions/${CARD_B}/feedback`,
    ]) {
      const res = await request(t.app, "POST", url, "owner-a", {
        verdict: "useful",
        event: "churned",
        occurred_at: "2026-10-01",
      });
      expect(res.status).toBe(404);
    }
  });
});

describe("requirement 3: roles", () => {
  const base = `/v1/workspaces/${WA}`;

  it("contributor and outsider are refused on every workspace surface", async () => {
    const t = world();
    for (const token of ["contributor-a", "outsider"] as const)
      for (const [method, template] of SURFACES) {
        const res = await request(t.app, method, concrete(WA, template), token);
        expect(res.status, `${token} ${method} ${template}`).toBe(403);
      }
    expect(t.reads.attention).not.toHaveBeenCalled();
    expect(t.reads.clientDetail).not.toHaveBeenCalled();
    expect(t.writes.calls).toEqual([]);
  });

  it("admin A reads exactly what owner A reads", async () => {
    const t = world();
    for (const path of ["/attention", "/clients", "/decisions", `/clients/${CA}`]) {
      const owner = await request(t.app, "GET", `${base}${path}`, "owner-a");
      const admin = await request(t.app, "GET", `${base}${path}`, "admin-a");
      expect(admin.status, path).toBe(owner.status);
      expect(admin.status).toBe(200);
      // `generated_at` is per-request; everything else must be identical.
      const ownerBody = await owner.json();
      const adminBody = await admin.json();
      delete (ownerBody as { generated_at?: string }).generated_at;
      delete (adminBody as { generated_at?: string }).generated_at;
      expect(adminBody).toEqual(ownerBody);
    }
  });

  it("no credentials is 401, never 403 or data", async () => {
    const t = world();
    for (const url of [`${base}/clients`, `${base}/attention`]) {
      const res = await request(t.app, "GET", url, null);
      expect(res.status).toBe(401);
    }
  });
});

describe("requirement 7: identifiers and paging", () => {
  const base = `/v1/workspaces/${WA}`;

  it("malformed client, evidence and outcome ids are 422 before any read", async () => {
    const t = world();
    for (const bad of ["not-a-uuid", "12345", CA.slice(0, 8), `${CA}extra`]) {
      expect((await request(t.app, "GET", `${base}/clients/${bad}`, "owner-a")).status).toBe(422);
      expect((await request(t.app, "GET", `${base}/evidence/${bad}`, "owner-a")).status).toBe(422);
      expect((await request(t.app, "DELETE", `${base}/outcomes/${bad}`, "owner-a")).status).toBe(
        422,
      );
    }
    expect(t.reads.clientDetail).not.toHaveBeenCalled();
    expect(t.writes.calls).toEqual([]);
  });

  it("nil and foreign-format ids never return data", async () => {
    const t = world();
    const nil = "00000000-0000-0000-0000-000000000000";
    const mixed = CA.toUpperCase();
    for (const id of [nil, mixed]) {
      // Both are valid UUIDs to the path guard, so the store decides: nothing to find is a 404.
      const res = await request(t.app, "GET", `${base}/clients/${id}`, "owner-a");
      expect(res.status).toBe(404);
      expect(await res.text()).not.toContain(NAME_CA);
    }
    const nilWorkspace = await request(t.app, "GET", `/v1/workspaces/${nil}/attention`, "owner-a");
    // A nil workspace is a valid UUID with no membership, so the role guard refuses it.
    expect(nilWorkspace.status).toBe(403);
    expect(await nilWorkspace.text()).not.toContain(NAME_CA);
  });

  it("a tampered cursor is 422 and a foreign cursor never returns A data", async () => {
    const t = world();
    expect(
      (await request(t.app, "GET", `${base}/clients/${CA}/timeline?cursor=bogus`, "owner-a"))
        .status,
    ).toBe(422);
    const cursor = Buffer.from(`2026-10-01T00:00:00.000000Z|signal:${EA}`).toString("base64url");
    // Replayed on B by its own owner: the store pages B's rows, so only B data may come back.
    const res = await request(
      t.app,
      "GET",
      `/v1/workspaces/${WB}/clients/${CB}/timeline?cursor=${cursor}`,
      "owner-b",
    );
    expect(res.status).toBe(200);
    const text = await res.text();
    for (const leak of [WA, CA, NAME_CA, "Ignore previous"]) expect(text).not.toContain(leak);
  });

  it("an oversize or zero limit is refused where the route paginates", async () => {
    const t = world();
    expect((await request(t.app, "GET", `${base}/decisions?limit=101`, "owner-a")).status).toBe(
      422,
    );
    expect(
      (await request(t.app, "GET", `${base}/clients/${CA}/evidence?limit=101`, "owner-a")).status,
    ).toBe(422);
    expect(
      (await request(t.app, "GET", `${base}/clients/${CA}/evidence?limit=0`, "owner-a")).status,
    ).toBe(422);
  });
});

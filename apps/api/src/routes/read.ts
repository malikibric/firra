import {
  routinesResponse,
  routineKind,
  routineEnabledRequest,
  decisionsResponse,
  decisionFilters,
  decisionStatus,
  feedbackRequest,
  outcomeRequest,
  mutationResponse,
  replayRunsResponse,
  replayClientResponse,
  timelineResponse,
  evidenceResponse,
  evidenceItem,
  attentionResponse,
  changesQuery,
  changesResponse,
  meetingsResponse,
  clientDetailResponse,
  clientListStatus,
  clientsResponse,
  openApiDocument,
} from "@firra/contracts";
import { Hono } from "hono";
import { z } from "zod";
import type { Context } from "hono";
import type { UserTokenVerifier } from "../auth/tokens.js";
import { parsePage } from "../lib/read-page.js";
import { clientIp } from "../lib/client-ip.js";
import { WriteForbiddenError } from "../lib/write-store.js";
import type { ReadCaller, ReadStore } from "../lib/read-store.js";
import type { ApiRepository } from "../lib/repository.js";
import type { AppEnv } from "../lib/types.js";
import { requireUser } from "../middleware/auth.js";
import { v1Guard } from "./v1-guard.js";
import { apiFailure } from "../middleware/v1-envelope.js";

interface Deps {
  repo: ApiRepository;
  verifyUser: UserTokenVerifier;
  readStore: ReadStore | null;
  userRateLimitPerMinute: number;
  release: string;
  now?: () => Date;
}

const INET = /^[0-9a-fA-F:.]{2,45}$/;

/** What a read handler gives back once it has the data. */
export interface ReadResult {
  body: unknown;
  /** Rows returned; the only thing besides ids that the audit row records. */
  count: number;
  target?: { type: string; id: string };
}

interface ReadContext {
  c: Context<AppEnv>;
  store: ReadStore;
  caller: ReadCaller;
  workspaceId: string;
  now: Date;
}

/** A body is the result, `null` is a 404, a `Response` (e.g. 422) is returned as is, unaudited. */
type ReadHandler = (ctx: ReadContext) => Promise<ReadResult | Response | null>;

/**
 * Read models for owners and admins under /v1/workspaces/:workspaceId. Every read runs as the
 * caller (RLS), is audited before data is returned (a failed audit write is a 500 without data)
 * and answers with the `/v1` error model (docs/plans/c-api-v1.md).
 */
export function readRoutes(deps: Deps): Hono<AppEnv> {
  const app = new Hono<AppEnv>();
  const now = deps.now ?? (() => new Date());

  app.get("/openapi.json", requireUser(deps.verifyUser), (c) =>
    c.json(openApiDocument(deps.release)),
  );

  const chain = v1Guard(deps);

  function audited(path: string, action: string, handler: ReadHandler): void {
    auditedRead(app, deps, chain, path, action, async (ctx) => {
      if (!deps.readStore)
        return apiFailure(ctx.c, 503, "reads_not_configured", "Reads are not configured here.");
      return handler({ ...ctx, store: deps.readStore, now: now() });
    });
  }

  audited("/routines", "read.routines", async ({ store, caller, workspaceId, now: at }) => {
    const body = routinesResponse.parse({
      generated_at: at.toISOString(),
      items: await store.routines(caller, workspaceId),
    });
    return { body, count: body.items.length };
  });
  app.put("/workspaces/:workspaceId/routines/:kind", ...chain, async (c) => {
    const store = deps.readStore?.writes;
    if (!store)
      return apiFailure(c, 503, "reads_not_configured", "Writes are not configured here.");
    if (c.req.header("content-type")?.split(";")[0]?.trim().toLowerCase() !== "application/json")
      return apiFailure(c, 422, "invalid_request", "Content-Type must be application/json.");
    const kind = routineKind.safeParse(c.req.param("kind"));
    const input: unknown = await c.req.json().catch(() => undefined);
    const request = routineEnabledRequest.safeParse(input);
    if (!kind.success || !request.success)
      return apiFailure(c, 422, "invalid_request", "Invalid routine switch.");
    const caller = {
      userId: c.get("user").id,
      token: c.req.header("authorization")?.slice("Bearer ".length).trim() ?? "",
    };
    try {
      const id = await store.setRoutineEnabled(
        caller,
        c.req.param("workspaceId"),
        kind.data,
        request.data,
      );
      return c.json(mutationResponse.parse({ id }));
    } catch (error) {
      if (error instanceof WriteForbiddenError)
        return apiFailure(c, 404, "not_found", "Not found.");
      throw error;
    }
  });

  audited("/decisions", "read.decisions", async ({ c, store, caller, workspaceId, now: at }) => {
    const status = decisionStatus.safeParse(c.req.query("status") ?? "new");
    const query = parsePage(c.req.query("limit"), c.req.query("cursor"), true);
    const filters = decisionFilters.safeParse({
      importance: c.req.query("importance"),
      kind: c.req.query("kind"),
    });
    if (!status.success || !query || !filters.success)
      return apiFailure(c, 422, "invalid_request", "Invalid list parameters.");
    const body = decisionsResponse.parse(
      await store.decisions(caller, workspaceId, status.data, query, at, filters.data),
    );
    return { body, count: body.items.length };
  });

  for (const operation of ["feedback", "outcome", "delete"] as const) {
    const path =
      operation === "feedback"
        ? "/decisions/:cardId/feedback"
        : operation === "outcome"
          ? "/clients/:clientId/outcomes"
          : "/outcomes/:outcomeId";
    app.on(
      operation === "delete" ? "DELETE" : "POST",
      `/workspaces/:workspaceId${path}`,
      ...chain,
      async (c) => {
        const store = deps.readStore?.writes;
        if (!store)
          return apiFailure(c, 503, "reads_not_configured", "Writes are not configured here.");
        // A DELETE has no body; only the two POSTs must declare JSON.
        if (
          operation !== "delete" &&
          c.req.header("content-type")?.split(";")[0]?.trim().toLowerCase() !== "application/json"
        )
          return apiFailure(c, 422, "invalid_request", "Content-Type must be application/json.");
        const caller = {
          userId: c.get("user").id,
          token: c.req.header("authorization")?.slice("Bearer ".length).trim() ?? "",
        };
        const workspaceId = c.req.param("workspaceId");
        try {
          if (operation === "delete") {
            await store.deleteOutcome(caller, workspaceId, c.req.param("outcomeId"));
            return c.body(null, 204);
          }
          const input: unknown = await c.req.json().catch(() => undefined);
          let id: string;
          if (operation === "feedback") {
            const parsed = feedbackRequest.safeParse(input);
            if (!parsed.success)
              return apiFailure(c, 422, "invalid_request", "Invalid feedback body.");
            id = await store.feedback(caller, workspaceId, c.req.param("cardId"), parsed.data);
          } else {
            const parsed = outcomeRequest.safeParse(input);
            if (!parsed.success)
              return apiFailure(c, 422, "invalid_request", "Invalid outcome body.");
            id = await store.outcome(caller, workspaceId, c.req.param("clientId"), parsed.data);
          }
          return c.json(mutationResponse.parse({ id }));
        } catch (error) {
          if (error instanceof WriteForbiddenError)
            return apiFailure(c, 404, "not_found", "Not found.");
          throw error;
        }
      },
    );
  }

  audited("/replay/runs", "read.replay_runs", async ({ store, caller, workspaceId, now: at }) => {
    const items = await store.replayRuns(caller, workspaceId, at);
    const body = replayRunsResponse.parse({ generated_at: at.toISOString(), items });
    return { body, count: body.items.length };
  });
  audited(
    "/replay/runs/:runId/clients/:clientId",
    "read.replay_client",
    async ({ c, store, caller, workspaceId, now: at }) => {
      const clientId = c.req.param("clientId") ?? "";
      const result = await store.replayClient(
        caller,
        workspaceId,
        c.req.param("runId") ?? "",
        clientId,
        at,
      );
      if (!result) return null;
      return {
        body: replayClientResponse.parse(result),
        count: 1,
        target: { type: "client", id: clientId },
      };
    },
  );

  audited("/changes", "read.changes", async ({ c, store, caller, workspaceId, now: at }) => {
    const query = changesQuery.safeParse(c.req.query());
    const since = query.success
      ? (query.data.since ?? new Date(at.getTime() - 7 * 86_400_000).toISOString().slice(0, 10))
      : null;
    const oldest = new Date(at.getTime() - 31 * 86_400_000).toISOString().slice(0, 10);
    if (
      !since ||
      !z.iso.date().safeParse(since).success ||
      since < oldest ||
      since > at.toISOString().slice(0, 10)
    )
      return apiFailure(
        c,
        422,
        "invalid_request",
        "Since must be a valid date within the last 31 days.",
      );
    const items = await store.changes(caller, workspaceId, since, at);
    const body = changesResponse.parse({ generated_at: at.toISOString(), since, items });
    return { body, count: body.items.length };
  });

  audited("/attention", "read.attention", async ({ store, caller, workspaceId, now: at }) => {
    const items = await store.attention(caller, workspaceId, at);
    const body = attentionResponse.parse({ generated_at: at.toISOString(), items });
    return { body, count: body.items.length };
  });

  audited(
    "/meetings/upcoming",
    "read.meeting_briefs",
    async ({ store, caller, workspaceId, now: at }) => {
      const items = await store.upcomingMeetings(caller, workspaceId, at);
      const body = meetingsResponse.parse({ generated_at: at.toISOString(), items });
      return { body, count: body.items.length };
    },
  );

  audited("/clients", "read.clients", async ({ c, store, caller, workspaceId, now: at }) => {
    const status = clientListStatus.safeParse(c.req.query("status") ?? "active");
    if (!status.success) {
      return apiFailure(c, 422, "invalid_request", "Status must be active, churned or all.");
    }
    const items = await store.clients(caller, workspaceId, status.data, at);
    const body = clientsResponse.parse({ generated_at: at.toISOString(), items });
    return { body, count: body.items.length };
  });

  audited(
    "/clients/:clientId",
    "read.client_detail",
    async ({ c, store, caller, workspaceId, now: at }) => {
      const clientId = c.req.param("clientId") ?? "";
      const client = await store.clientDetail(caller, workspaceId, clientId, at);
      if (!client) return null;
      const body = clientDetailResponse.parse({ generated_at: at.toISOString(), client });
      return { body, count: 1, target: { type: "client", id: clientId } };
    },
  );

  for (const kind of ["timeline", "evidence"] as const) {
    audited(
      `/clients/:clientId/${kind}`,
      kind === "timeline" ? "read.client_timeline" : "read.client_evidence",
      async ({ c, store, caller, workspaceId, now: at }) => {
        const query = parsePage(c.req.query("limit"), c.req.query("cursor"), kind === "evidence");
        if (!query) return apiFailure(c, 422, "invalid_request", "Invalid pagination parameters.");
        const clientId = c.req.param("clientId") ?? "";
        const result =
          kind === "timeline"
            ? await store.timeline(caller, workspaceId, clientId, query, at)
            : await store.clientEvidence(caller, workspaceId, clientId, query, at);
        if (!result) return null;
        const body = (kind === "timeline" ? timelineResponse : evidenceResponse).parse(result);
        return { body, count: body.items.length, target: { type: "client", id: clientId } };
      },
    );
  }
  audited(
    "/evidence/:evidenceId",
    "read.evidence",
    async ({ c, store, caller, workspaceId, now: at }) => {
      const evidenceId = c.req.param("evidenceId") ?? "";
      const result = await store.evidence(caller, workspaceId, evidenceId, at);
      if (!result) return null;
      return {
        body: evidenceItem.parse(result),
        count: 1,
        target: { type: "evidence", id: evidenceId },
      };
    },
  );
  return app;
}

/** Shared audited(...) read path for the intelligence and setup routers. Failed audits return no data. */
export function auditedRead(
  app: Hono<AppEnv>,
  deps: Pick<Deps, "repo">,
  chain: ReturnType<typeof v1Guard>,
  path: string,
  action: string,
  handler: (ctx: {
    c: Context<AppEnv>;
    caller: ReadCaller;
    workspaceId: string;
  }) => Promise<ReadResult | Response | null>,
): void {
  app.get(`/workspaces/:workspaceId${path}`, ...chain, async (c) => {
    const user = c.get("user");
    const workspaceId = c.req.param("workspaceId");
    const caller = {
      token: c.req.header("authorization")?.slice("Bearer ".length).trim() ?? "",
      userId: user.id,
    };
    const result = await handler({ c, caller, workspaceId });
    if (result instanceof Response) return result;
    if (result === null) return apiFailure(c, 404, "not_found", "Not found.");
    const ip = clientIp(c.req.header("x-forwarded-for"));
    await deps.repo.writeAudit({
      workspaceId,
      actorId: user.id,
      action,
      targetType: result.target?.type,
      targetId: result.target?.id,
      ip: INET.test(ip) ? ip : null,
      userAgent: c.req.header("user-agent") ?? null,
      metadata: { count: result.count },
    });
    return c.json(result.body);
  });
}

import type {
  ChangeItem,
  DecisionsPage,
  DecisionStatus,
  ReplayRun,
  ReplayClient,
  EvidenceItem,
  EvidencePage,
  TimelinePage,
  AttentionItem,
  ClientDetail,
  ClientListItem,
  ClientListStatus,
  MeetingBriefItem,
  DecisionFilters,
  RoutineItem,
} from "@firra/contracts";
import { z } from "zod";
import type { ReplayDatabase } from "./replay-schema.js";
import { createClient } from "@supabase/supabase-js";
import {
  routineKind,
  routineItem,
  decisionsResponse,
  replayRun,
  replayNarrative,
  replayClientResponse,
  timelineEvent,
  evidenceItem,
} from "@firra/contracts";
import { SupabaseWriteStore, type WriteStore } from "./write-store.js";
import { instant, page } from "./read-page.js";
import type { PageQuery } from "./read-page.js";
import { buildChanges, changeAssessment, changeSignal, changeSignalEvent } from "./changes.js";
import { buildAttention } from "./attention.js";
import { buildMeetings } from "./meetings.js";
import { buildClientDetail, buildClientListItem, buildStateHistory } from "./clients.js";
import type { HistoryRow } from "./clients.js";

/** Who is reading. Reads run with this token so Postgres RLS is the authorization layer. */
export interface ReadCaller {
  token: string;
  userId: string;
}

/**
 * Read models of `/v1` (docs/plans/c-api-v1.md, C-D5). The first implementation reads as the
 * caller through RLS; a second one for MCP tokens (milestone F) sits behind the same interface.
 */
export interface ReadStore {
  readonly writes?: WriteStore;
  routines(caller: ReadCaller, workspaceId: string): Promise<RoutineItem[]>;
  decisions(
    caller: ReadCaller,
    workspaceId: string,
    status: DecisionStatus,
    query: PageQuery,
    now: Date,
    filters?: DecisionFilters,
  ): Promise<DecisionsPage>;
  replayRuns(caller: ReadCaller, workspaceId: string, now: Date): Promise<ReplayRun[]>;
  replayClient(
    caller: ReadCaller,
    workspaceId: string,
    runId: string,
    clientId: string,
    now: Date,
  ): Promise<ReplayClient | null>;
  timeline(
    caller: ReadCaller,
    workspaceId: string,
    clientId: string,
    query: PageQuery,
    now: Date,
  ): Promise<TimelinePage | null>;
  clientEvidence(
    caller: ReadCaller,
    workspaceId: string,
    clientId: string,
    query: PageQuery,
    now: Date,
  ): Promise<EvidencePage | null>;
  evidence(
    caller: ReadCaller,
    workspaceId: string,
    evidenceId: string,
    now: Date,
  ): Promise<EvidenceItem | null>;
  changes(caller: ReadCaller, workspaceId: string, since: string, now: Date): Promise<ChangeItem[]>;
  attention(caller: ReadCaller, workspaceId: string, now: Date): Promise<AttentionItem[]>;
  /** Briefs for client meetings that have not started, soonest first (at most 10). */
  upcomingMeetings(caller: ReadCaller, workspaceId: string, now: Date): Promise<MeetingBriefItem[]>;
  clients(
    caller: ReadCaller,
    workspaceId: string,
    status: ClientListStatus,
    now: Date,
  ): Promise<ClientListItem[]>;
  clientDetail(
    caller: ReadCaller,
    workspaceId: string,
    clientId: string,
    now: Date,
  ): Promise<ClientDetail | null>;
}

const ASSESSMENT_WINDOW_DAYS = 60;
const ASSESSMENT_LIMIT = 5000;
const DAY_MS = 86_400_000;
const CLIENT_COLUMNS = "id, name, status, policy, mrr, currency, renewal_date, notice_period_days";
const DETAIL_ASSESSMENT_COLUMNS =
  "as_of, risk_state, previous_risk_state, state_since, expansion_state, strength, play, drivers, caveats";
const DETAIL_LIMIT = 500;
// More than the 10 returned, because cancelled, rescheduled and restricted briefs are filtered out.
const MEETING_CANDIDATES = 50;

function readData<T>(result: { data: T | null; error: { code: string } | null }): T {
  if (result.error || result.data === null) throw new Error("client read failed");
  return result.data;
}

export class SupabaseReadStore implements ReadStore {
  readonly writes: WriteStore;
  constructor(
    private readonly url: string,
    private readonly publishableKey: string,
  ) {
    this.writes = new SupabaseWriteStore(url, publishableKey);
  }

  private asCaller(caller: ReadCaller) {
    return createClient<ReplayDatabase>(this.url, this.publishableKey, {
      auth: { persistSession: false, autoRefreshToken: false },
      global: { headers: { Authorization: `Bearer ${caller.token}` } },
    });
  }

  async routines(caller: ReadCaller, workspaceId: string): Promise<RoutineItem[]> {
    const rows = readData(
      await this.asCaller(caller)
        .from("routine")
        .select("kind, enabled, routine_run(scheduled_for, status, finished_at, counts)")
        .eq("workspace_id", workspaceId)
        .eq("routine_run.workspace_id", workspaceId)
        .order("scheduled_for", { referencedTable: "routine_run", ascending: false })
        .limit(1, { referencedTable: "routine_run" }),
    );
    return routineKind.options.map((kind) => {
      const row = rows.find((r) => r.kind === kind);
      const last = row?.routine_run[0];
      return routineItem.parse({
        kind,
        enabled: row?.enabled ?? true,
        created: row !== undefined,
        last_run: last
          ? { ...last, finished_at: last.finished_at ? instant(last.finished_at) : null }
          : null,
      });
    });
  }

  async decisions(
    caller: ReadCaller,
    workspaceId: string,
    status: DecisionStatus,
    query: PageQuery,
    _now: Date,
    filters: DecisionFilters = {},
  ): Promise<DecisionsPage> {
    let request = this.asCaller(caller)
      .from("decision_card")
      .select(
        "id, client_id, week, kind, importance, surfaced_on, title, body, play, strength, priority, status, created_at, facts, client!inner(name, policy), decision_evidence(evidence_id), decision_feedback(verdict, action, note)",
      )
      .eq("workspace_id", workspaceId)
      .in("client.policy", ["full", "metadata_only"])
      .eq("decision_feedback.user_id", caller.userId)
      .order("created_at", { ascending: false })
      .order("id", { ascending: false })
      .limit(query.limit + 1);
    if (status !== "all") request = request.eq("status", status);
    if (filters.importance) request = request.eq("importance", filters.importance);
    if (filters.kind) request = request.eq("kind", filters.kind);
    if (query.cursor)
      request = request.or(
        `created_at.lt.${query.cursor.occurred_at},and(created_at.eq.${query.cursor.occurred_at},id.lt.${query.cursor.id})`,
      );
    const rows = readData(await request);
    // Metadata cards are worded from counters/dates by the worker; never serve evidence-based text.
    for (const row of rows) {
      if (
        row.client.policy === "metadata_only" &&
        (!z.array(z.object({ basis: z.literal("metadata") })).safeParse(row.facts).success ||
          row.decision_evidence.length > 0)
      )
        throw new Error("decision policy check failed");
    }
    const result = page(
      rows.map((r) => ({ ...r, occurred_at: r.created_at })),
      query,
    );
    return decisionsResponse.parse({
      items: result.items.map((r) => ({
        ...r,
        created_at: instant(r.created_at),
        client_name: r.client.name,
        evidence_ids: r.decision_evidence.map((e) => e.evidence_id),
        feedback: r.decision_feedback[0] ?? null,
      })),
      next_cursor: result.next_cursor,
    });
  }

  async replayRuns(caller: ReadCaller, workspaceId: string, _now: Date): Promise<ReplayRun[]> {
    const db = this.asCaller(caller);
    const rows = z
      .array(
        replayRun.omit({ totals: true, narrated: true }).extend({
          summary: z.object({ totals: z.unknown().optional() }).nullable(),
          replay_narrative: z.array(z.object({ version: z.number().int() })),
        }),
      )
      .parse(
        readData(
          await db
            .from("replay_run")
            .select(
              "id, window_start, window_end, step_days, status, finished_at, summary, replay_narrative(version)",
            )
            .eq("workspace_id", workspaceId)
            .order("created_at", { ascending: false })
            .order("id")
            .limit(500),
        ),
      );
    return rows.map((row) =>
      replayRun.parse({
        ...row,
        totals: row.summary?.totals ?? null,
        narrated: row.status === "succeeded" && row.replay_narrative.some((n) => n.version === 1),
      }),
    );
  }

  async replayClient(
    caller: ReadCaller,
    workspaceId: string,
    runId: string,
    clientId: string,
    now: Date,
  ): Promise<ReplayClient | null> {
    const db = this.asCaller(caller);
    const client = await this.visible(db, workspaceId, clientId);
    if (!client) return null;
    // Migration 19 precedes the lead-owned generated types; validate this relation with zod.
    const result = await db
      .from("replay_narrative")
      .select("version, narrative, replay_run!inner(status)")
      .eq("workspace_id", workspaceId)
      .eq("replay_run_id", runId)
      .eq("client_id", clientId)
      .eq("version", 1)
      .eq("replay_run.status", "succeeded")
      .maybeSingle();
    if (result.error) throw new Error("replay read failed");
    if (!result.data) return null;
    const stored = z.object({ narrative: replayNarrative }).parse(result.data);
    const narrative = stored.narrative;
    // Policy deletion is authoritative; reject any incompatible stored row as defense in depth.
    const drivers = [
      ...narrative.visible_at_detection,
      ...narrative.detection_timing,
      ...narrative.transitions.flatMap((t) => [...t.new_drivers, ...t.active_drivers]),
      ...narrative.events.flatMap((e) => (e.driver ? [e.driver] : [])),
    ];
    if (
      client.policy === "metadata_only" &&
      (drivers.some((d) => d.basis !== "metadata" || d.evidence_ids.length) ||
        narrative.events.some((e) => e.grouping_ids.length))
    )
      return null;
    const weeks = [];
    for (let offset = 0; ; offset += 500) {
      const batch = readData(
        await db
          .from("account_assessment")
          .select("as_of, risk_state, expansion_state, play, strength")
          .eq("workspace_id", workspaceId)
          .eq("client_id", clientId)
          .eq("replay_run_id", runId)
          .eq("run_kind", "replay")
          .order("as_of")
          .range(offset, offset + 499),
      );
      weeks.push(
        ...batch.map((w) => ({
          as_of: w.as_of,
          risk: w.risk_state,
          expansion: w.expansion_state,
          play: w.play,
          strength: w.strength,
        })),
      );
      if (batch.length < 500) break;
    }
    // Recheck after the multi-request read so a downgrade cannot mix old/new policy data.
    const current = await this.visible(db, workspaceId, clientId);
    if (current?.policy !== client.policy) return null;
    return replayClientResponse.parse({
      generated_at: now.toISOString(),
      run_id: runId,
      client_id: clientId,
      version: 1,
      narrative,
      weeks,
      evidence_ids: [
        ...new Set([
          ...drivers.flatMap((d) => d.evidence_ids),
          ...narrative.events.flatMap((e) => e.grouping_ids),
        ]),
      ],
    });
  }

  async changes(
    caller: ReadCaller,
    workspaceId: string,
    since: string,
    now: Date,
  ): Promise<ChangeItem[]> {
    const db = this.asCaller(caller);
    const today = now.toISOString().slice(0, 10);
    // Page every stream before the final cap: unchanged rows must not crowd out newer changes.
    async function all<T>(
      request: (offset: number) => PromiseLike<{
        data: T[] | null;
        error: { code: string } | null;
      }>,
    ): Promise<T[]> {
      const rows: T[] = [];
      for (let offset = 0; ; offset += 500) {
        const batch = readData(await request(offset));
        rows.push(...batch);
        if (batch.length < 500) return rows;
      }
    }
    const assessmentColumns = "client_id, as_of, risk_state, previous_risk_state, expansion_state";
    const eventColumns = "id, client_id, signal_id, as_of, created_at, change, severity";
    const signalColumns = "type, family, basis, signal_evidence(evidence_id)";
    const [assessments, clients, events, signals] = await Promise.all([
      all((offset) =>
        db
          .from("account_assessment")
          .select(assessmentColumns)
          .eq("workspace_id", workspaceId)
          .eq("run_kind", "live")
          .gte("as_of", since)
          .lte("as_of", today)
          .order("as_of")
          .order("id")
          .range(offset, offset + 499),
      ),
      all((offset) =>
        db
          .from("client")
          .select(`id, name, status, policy, account_assessment(${assessmentColumns})`)
          .eq("workspace_id", workspaceId)
          .eq("status", "active")
          .in("policy", ["full", "metadata_only"])
          .eq("account_assessment.run_kind", "live")
          .lt("account_assessment.as_of", since)
          .order("as_of", { referencedTable: "account_assessment", ascending: false })
          .limit(1, { referencedTable: "account_assessment" })
          .order("id")
          .range(offset, offset + 499),
      ),
      all((offset) =>
        db
          .from("signal_event")
          .select(`${eventColumns}, signal!inner(${signalColumns})`)
          .eq("workspace_id", workspaceId)
          .gte("as_of", `${since}T00:00:00Z`)
          .lte("as_of", now.toISOString())
          .order("as_of")
          .order("created_at")
          .order("id")
          .range(offset, offset + 499),
      ),
      all((offset) =>
        db
          .from("signal")
          .select(`${signalColumns}, signal_event(${eventColumns})`)
          .eq("workspace_id", workspaceId)
          .lt("signal_event.as_of", `${since}T00:00:00Z`)
          .order("as_of", { referencedTable: "signal_event", ascending: false })
          .order("created_at", { referencedTable: "signal_event", ascending: false })
          .order("id", { referencedTable: "signal_event", ascending: false })
          .limit(1, { referencedTable: "signal_event" })
          .order("id")
          .range(offset, offset + 499),
      ),
    ]);
    const clientRows = z
      .array(
        z.object({
          id: z.uuid(),
          name: z.string(),
          status: z.enum(["active", "churned", "prospect"]),
          policy: z.string(),
          account_assessment: z.array(changeAssessment),
        }),
      )
      .parse(clients);
    const signalRows = z
      .array(
        changeSignal.extend({
          signal_event: z.array(changeSignalEvent.omit({ signal: true })),
        }),
      )
      .parse(signals);
    return buildChanges(
      [
        ...z.array(changeAssessment).parse(assessments),
        ...clientRows.flatMap((c) => c.account_assessment),
      ],
      [
        ...z.array(changeSignalEvent).parse(events),
        ...signalRows.flatMap((s) => s.signal_event.map((e) => ({ ...e, signal: s }))),
      ],
      clientRows,
      since,
      now,
    );
  }

  async attention(caller: ReadCaller, workspaceId: string, now: Date): Promise<AttentionItem[]> {
    const db = this.asCaller(caller);
    const since = new Date(now.getTime() - ASSESSMENT_WINDOW_DAYS * DAY_MS)
      .toISOString()
      .slice(0, 10);
    const [assessments, clients] = await Promise.all([
      db
        .from("account_assessment")
        .select(
          "client_id, as_of, risk_state, previous_risk_state, state_since, expansion_state, strength, play, drivers",
        )
        .eq("workspace_id", workspaceId)
        .eq("run_kind", "live")
        .gte("as_of", since)
        .order("as_of", { ascending: false })
        .limit(ASSESSMENT_LIMIT),
      db
        .from("client")
        .select("id, name, status, policy")
        .eq("workspace_id", workspaceId)
        .eq("status", "active"),
    ]);
    if (assessments.error) throw new Error(`attention read failed: ${assessments.error.code}`);
    if (clients.error) throw new Error(`attention read failed: ${clients.error.code}`);
    return buildAttention(assessments.data, clients.data, now);
  }

  async upcomingMeetings(
    caller: ReadCaller,
    workspaceId: string,
    now: Date,
  ): Promise<MeetingBriefItem[]> {
    const db = this.asCaller(caller);
    const briefs = await db
      .from("meeting_brief")
      .select("id, calendar_event_id, client_id, starts_at, basis, content")
      .eq("workspace_id", workspaceId)
      .gt("starts_at", now.toISOString())
      .order("starts_at", { ascending: true })
      .limit(MEETING_CANDIDATES);
    if (briefs.error) throw new Error(`meetings read failed: ${briefs.error.code}`);
    if (briefs.data.length === 0) return [];
    const [events, clients] = await Promise.all([
      db
        .from("calendar_event")
        .select("id, status, starts_at")
        .eq("workspace_id", workspaceId)
        .in("id", [...new Set(briefs.data.map((b) => b.calendar_event_id))]),
      db
        .from("client")
        .select("id, name, status, policy")
        .eq("workspace_id", workspaceId)
        .in("id", [...new Set(briefs.data.map((b) => b.client_id))]),
    ]);
    if (events.error) throw new Error(`meetings read failed: ${events.error.code}`);
    if (clients.error) throw new Error(`meetings read failed: ${clients.error.code}`);
    return buildMeetings(briefs.data, events.data, clients.data, now);
  }

  async clients(
    caller: ReadCaller,
    workspaceId: string,
    status: ClientListStatus,
    _now: Date,
  ): Promise<ClientListItem[]> {
    const db = this.asCaller(caller);
    // Limit on the embedded relation is per client, so busy clients cannot crowd out older assessments.
    let query = db
      .from("client")
      .select(
        `${CLIENT_COLUMNS}, account_assessment(as_of, risk_state, state_since, expansion_state, play)`,
      )
      .eq("workspace_id", workspaceId)
      .in("policy", ["full", "metadata_only"])
      .eq("account_assessment.run_kind", "live")
      .order("as_of", { referencedTable: "account_assessment", ascending: false })
      .limit(1, { referencedTable: "account_assessment" })
      .order("name")
      .order("id")
      .limit(500);
    if (status !== "all") query = query.eq("status", status);
    return readData(await query).map((row) =>
      buildClientListItem(row, row.account_assessment[0] ?? null),
    );
  }

  async clientDetail(
    caller: ReadCaller,
    workspaceId: string,
    clientId: string,
    _now: Date,
  ): Promise<ClientDetail | null> {
    const db = this.asCaller(caller);
    const clientResult = await db
      .from("client")
      .select(CLIENT_COLUMNS)
      .eq("workspace_id", workspaceId)
      .eq("id", clientId)
      .in("policy", ["full", "metadata_only"])
      .maybeSingle();
    if (clientResult.error) throw new Error("client read failed");
    const client = clientResult.data;
    if (!client) return null;
    const metadataOnly = client.policy === "metadata_only";
    const empty = { data: [], error: null };
    const [assessment, stakeholders, commitments, goals, needs, outcomes, card] = await Promise.all(
      [
        db
          .from("account_assessment")
          .select(DETAIL_ASSESSMENT_COLUMNS)
          .eq("workspace_id", workspaceId)
          .eq("client_id", clientId)
          .eq("run_kind", "live")
          .order("as_of", { ascending: false })
          .limit(1),
        db
          .from("person")
          .select("display_name, role_title, is_decision_maker, is_champion, last_seen_at")
          .eq("workspace_id", workspaceId)
          .eq("client_id", clientId)
          .eq("side", "client")
          .is("merged_into_person_id", null)
          .order("display_name")
          .order("id")
          .limit(DETAIL_LIMIT),
        metadataOnly
          ? empty
          : db
              .from("commitment")
              .select(
                "owner_side, text, due_date, status, commitment_evidence(evidence_id, created_at)",
              )
              .order("created_at", { referencedTable: "commitment_evidence", ascending: false })
              .order("evidence_id", { referencedTable: "commitment_evidence" })
              .limit(5, { referencedTable: "commitment_evidence" })
              .eq("workspace_id", workspaceId)
              .eq("client_id", clientId)
              .in("status", ["open", "late"])
              .order("due_date")
              .order("id")
              .limit(DETAIL_LIMIT),
        metadataOnly
          ? empty
          : db
              .from("client_goal")
              .select("text, metric, stated_at, client_goal_evidence(evidence_id, created_at)")
              .order("created_at", { referencedTable: "client_goal_evidence", ascending: false })
              .order("evidence_id", { referencedTable: "client_goal_evidence" })
              .limit(5, { referencedTable: "client_goal_evidence" })
              .eq("workspace_id", workspaceId)
              .eq("client_id", clientId)
              .order("stated_at", { ascending: false })
              .order("id")
              .limit(DETAIL_LIMIT),
        metadataOnly
          ? empty
          : db
              .from("latent_need")
              .select(
                "topic, mention_count, thread_count, first_seen_at, last_seen_at, latent_need_evidence(evidence_id, created_at)",
              )
              .order("created_at", { referencedTable: "latent_need_evidence", ascending: false })
              .order("evidence_id", { referencedTable: "latent_need_evidence" })
              .limit(5, { referencedTable: "latent_need_evidence" })
              .eq("workspace_id", workspaceId)
              .eq("client_id", clientId)
              .eq("status", "open")
              .order("last_seen_at", { ascending: false })
              .order("id")
              .limit(DETAIL_LIMIT),
        db
          .from("outcome")
          .select("event, occurred_at, notice_at, first_known_at, amount_change, currency")
          .eq("workspace_id", workspaceId)
          .eq("client_id", clientId)
          .order("occurred_at", { ascending: false })
          .order("id")
          .limit(DETAIL_LIMIT),
        db
          .from("client_card")
          .select("content")
          .eq("workspace_id", workspaceId)
          .eq("client_id", clientId)
          .order("version", { ascending: false })
          .limit(1),
      ],
    );
    const history: HistoryRow[] = [];
    // Page internally until twenty actual changes are found; unchanged daily rows are not a cap.
    for (let offset = 0; buildStateHistory(history).length < 20; offset += 100) {
      const page = readData(
        await db
          .from("account_assessment")
          .select("as_of, risk_state, previous_risk_state")
          .eq("workspace_id", workspaceId)
          .eq("client_id", clientId)
          .eq("run_kind", "live")
          .order("as_of", { ascending: false })
          .range(offset, offset + 99),
      );
      history.push(
        ...page.filter(
          (row) => row.previous_risk_state !== null && row.previous_risk_state !== row.risk_state,
        ),
      );
      if (page.length < 100) break;
    }
    return buildClientDetail(client, readData(assessment)[0] ?? null, history, {
      stakeholders: readData(stakeholders),
      commitments: readData(commitments),
      goals: readData(goals),
      needs: readData(needs),
      outcomes: readData(outcomes),
      cardContent: readData(card)[0]?.content,
    });
  }
  private async visible(
    db: ReturnType<SupabaseReadStore["asCaller"]>,
    workspaceId: string,
    clientId: string,
  ) {
    const result = await db
      .from("client")
      .select("policy")
      .eq("workspace_id", workspaceId)
      .eq("id", clientId)
      .in("policy", ["full", "metadata_only"])
      .maybeSingle();
    if (result.error) throw new Error("client read failed");
    return result.data;
  }

  async timeline(
    caller: ReadCaller,
    workspaceId: string,
    clientId: string,
    query: PageQuery,
    now: Date,
  ): Promise<TimelinePage | null> {
    const db = this.asCaller(caller);
    const client = await this.visible(db, workspaceId, clientId);
    if (!client) return null;
    const metadata = client.policy === "metadata_only";
    // Each stream uses its own dated keyset. Merge only after obtaining enough eligible events
    // from each stream; unchanged assessments and filtered policy rows cannot truncate a page.
    const streams = [
      {
        table: "account_assessment",
        date: "as_of",
        prefix: "state",
        columns: "id, as_of, risk_state, previous_risk_state",
        kind: "state_change",
      },
      {
        table: "signal_event",
        date: "as_of",
        prefix: "signal",
        columns:
          "id, as_of, change, severity, signal!inner(type, basis, signal_evidence(evidence_id))",
        kind: "signal",
      },
      {
        table: "outcome",
        date: "occurred_at",
        prefix: "outcome",
        columns: "id, occurred_at, event",
        kind: "outcome",
      },
      ...(!metadata
        ? ["created", "due", "closed"].map((change) => ({
            table: "commitment" as const,
            date: change === "created" ? "stated_at" : change === "due" ? "due_date" : "closed_at",
            prefix: `commitment_${change}`,
            columns: "id, stated_at, due_date, closed_at, owner_side, status",
            kind: "commitment",
            change,
          }))
        : []),
    ] as const;
    const rows = await Promise.all(
      streams.map(async (stream) => {
        const events = [];
        let boundary: { date: string; id: string } | null = null;
        while (events.length <= query.limit) {
          let request = db
            .from(stream.table)
            .select(stream.columns)
            .eq("workspace_id", workspaceId)
            .eq("client_id", clientId)
            .not(stream.date, "is", null)
            .order(stream.date, { ascending: false })
            .order("id", { ascending: false })
            .limit(100);
          if (stream.kind === "state_change") request = request.filter("run_kind", "eq", "live");
          if (metadata && stream.kind === "signal")
            request = request.eq("signal.basis", "metadata");
          const upper = query.cursor?.occurred_at ?? now.toISOString();
          request = request.lte(
            stream.date,
            (stream.date === "as_of" && stream.kind === "state_change") ||
              stream.date === "due_date" ||
              stream.kind === "outcome"
              ? upper.slice(0, 10)
              : upper,
          );
          if (boundary)
            request = request.or(
              `${stream.date}.lt.${boundary.date},and(${stream.date}.eq.${boundary.date},id.lt.${boundary.id})`,
            );
          const batch = readData(await request);
          // Dynamic selects are validated below, never returned as arbitrary JSON.
          const parsed = z.array(z.object({ id: z.uuid() }).catchall(z.unknown())).parse(batch);
          for (const row of parsed) {
            const occurred_at = instant(String(row[stream.date]));
            const base = {
              id: `${stream.prefix}:${row.id}`,
              client_id: clientId,
              occurred_at,
              kind: stream.kind,
            };
            let event;
            if (stream.kind === "state_change") {
              if (!row.previous_risk_state || row.previous_risk_state === row.risk_state) continue;
              event = { ...base, from: row.previous_risk_state, to: row.risk_state };
            } else if (stream.kind === "signal") {
              const signal = z
                .object({
                  type: z.string(),
                  basis: z.string(),
                  signal_evidence: z.array(z.object({ evidence_id: z.uuid() })),
                })
                .parse(row.signal);
              if (metadata && signal.basis !== "metadata") continue;
              event = {
                ...base,
                type: signal.type,
                change: row.change,
                severity: row.severity,
                evidence_ids:
                  signal.basis === "metadata"
                    ? []
                    : signal.signal_evidence.map((e) => e.evidence_id),
              };
            } else if (stream.kind === "outcome") event = { ...base, event: row.event };
            else
              event = {
                ...base,
                change: "change" in stream ? stream.change : "created",
                owner_side: row.owner_side,
                status: row.status,
              };
            const item = timelineEvent.parse(event);
            if (page([item], query).items.length) events.push(item);
          }
          const last = parsed.at(-1);
          if (!last || batch.length < 100) break;
          boundary = { date: String(last[stream.date]), id: last.id };
        }
        return events;
      }),
    );
    return page(rows.flat(), query);
  }

  async clientEvidence(
    caller: ReadCaller,
    workspaceId: string,
    clientId: string,
    query: PageQuery,
    now: Date,
  ): Promise<EvidencePage | null> {
    const db = this.asCaller(caller);
    const client = await this.visible(db, workspaceId, clientId);
    if (!client) return null;
    if (client.policy !== "full") return { items: [], next_cursor: null };
    const items: EvidenceItem[] = [];
    // At the cursor timestamp, apply the evidence UUID predicate per source. Older timestamps
    // need no UUID restriction. The per-source limit prevents PostgREST's nested row cap from
    // truncating evidence, while retaining enough candidates to fill the global page.
    const streams = query.cursor ? (["same", "older"] as const) : (["all"] as const);
    for (const stream of streams) {
      let boundary: { date: string; id: string } | null = null;
      for (;;) {
        let request = db
          .from("source_record")
          .select(
            "id, occurred_at, metadata, person!source_record_workspace_id_author_person_id_fkey(side, display_name, role_title), evidence!inner(id, client_id, quote, locator, deep_link)",
          )
          .eq("workspace_id", workspaceId)
          .eq("client_id", clientId)
          .order("occurred_at", { ascending: false })
          .order("id", { ascending: false })
          .order("id", { referencedTable: "evidence", ascending: false })
          .limit(query.limit + 1, { referencedTable: "evidence" })
          .limit(100);
        if (stream === "same" && query.cursor)
          request = request
            .eq("occurred_at", query.cursor.occurred_at)
            .lt("evidence.id", query.cursor.id);
        else if (stream === "older" && query.cursor)
          request = request.lt("occurred_at", query.cursor.occurred_at);
        else request = request.lte("occurred_at", now.toISOString());
        if (boundary)
          request = request.or(
            `occurred_at.lt.${boundary.date},and(occurred_at.eq.${boundary.date},id.lt.${boundary.id})`,
          );
        const batch = readData(await request);
        for (const row of batch)
          for (const evidence of row.evidence) items.push(this.shapeEvidence(evidence, row));
        const last = batch.at(-1);
        const result = page(items, query);
        // Read all sources at the final timestamp: evidence UUID ties span source records.
        if (
          !last ||
          batch.length < 100 ||
          (result.next_cursor &&
            instant(last.occurred_at) < (result.items.at(-1)?.occurred_at ?? ""))
        )
          break;
        boundary = { date: last.occurred_at, id: last.id };
      }
    }
    return page(items, query);
  }

  private shapeEvidence(evidence: unknown, source: unknown): EvidenceItem {
    const record = z
      .object({
        id: z.uuid(),
        occurred_at: z.string(),
        metadata: z.object({ subject: z.string().nullish() }),
        person: z
          .object({
            side: z.string(),
            display_name: z.string().nullable(),
            role_title: z.string().nullable(),
          })
          .nullable(),
      })
      .parse(source);
    return evidenceItem.parse({
      ...z
        .object({
          id: z.uuid(),
          client_id: z.uuid(),
          quote: z.string(),
          locator: z.unknown(),
          deep_link: z.string().nullable(),
        })
        .parse(evidence),
      occurred_at: instant(record.occurred_at),
      subject: record.metadata.subject?.trim().slice(0, 120) ?? null,
      source_ref: record.id.slice(0, 8),
      author_name: record.person?.side === "client" ? record.person.display_name : null,
      author_role: record.person?.side === "client" ? record.person.role_title : null,
    });
  }

  async evidence(
    caller: ReadCaller,
    workspaceId: string,
    evidenceId: string,
    _now: Date,
  ): Promise<EvidenceItem | null> {
    const db = this.asCaller(caller);
    const result = await db
      .from("evidence")
      .select(
        "id, client_id, quote, locator, deep_link, source_record!inner(id, occurred_at, metadata, person!source_record_workspace_id_author_person_id_fkey(side, display_name, role_title))",
      )
      .eq("workspace_id", workspaceId)
      .eq("id", evidenceId)
      .maybeSingle();
    if (result.error) throw new Error("evidence read failed");
    if (!result.data) return null;
    // `evidence` has no foreign key to `client` to embed: ask for the client's policy separately.
    const client = await this.visible(db, workspaceId, result.data.client_id);
    if (client?.policy !== "full") return null;
    return this.shapeEvidence(result.data, result.data.source_record);
  }
}

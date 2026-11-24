# T45 — Pre-meeting brief (F1) on the server

Goal: the Mac notch pill (T80–T83) shows a real brief before a client meeting. The brief is computed
and stored by the worker (rule: the worker persists every judgement), read through `/v1`, and shown
by the Mac app. Spec of the pill: the pill design spec.

## Decisions

| # | Topic | Decision | Status |
|---|---|---|---|
| M1 | Wording | Version 1 is **deterministic, no LLM**: sections and the 3 questions come from facts the worker already has (unknowns checklist, open commitments, unresolved complaints, stakeholder changes, renewal window, last held meeting). Every question names the item behind it. LLM wording (`meeting.v1`, must pass `verify`) is a later task behind the spend switch. | default (no spend); deviates from architecture.md "LLM writes the text" until M7 |
| M2 | Storage | Table `meeting_brief` as in `architecture.md` §3, plus `meeting_brief_evidence` (join to `evidence`). Unique `(calendar_event_id, starts_at)`: a rescheduled meeting gets a new brief, a cancelled one none. Writes: service role only. Reads: owner/admin through RLS, hidden for `excluded` clients (`private.client_visible`). | default (table is already in the approved data model) |
| M3 | Policy | `full` client: sections with evidence quotes (≤ 25 words, already verified). `metadata_only` client: counts and dates only, no quotes, no commitment/complaint text. `excluded`: no brief. | rule 8 |
| M4 | Trigger | The 15-minute sweep enqueues `brief.meetings` per workspace; it builds briefs for client `calendar_event`s with `status = 'scheduled'` starting within the lead time (constant 2 h in `services/worker/config/meeting.yaml`; no new workspace column in v1). Idempotent on `(calendar_event_id, starts_at)`. | default |
| M5 | Read model | `GET /v1/workspaces/:workspaceId/meetings/upcoming` → briefs for meetings that have not started, soonest first, max 10. Owner/admin only, audit `read.meeting_briefs` with `{ count }`, fail closed, `no-store`, RLS-as-caller. | default |
| M6 | Mac | `APIMeetingBriefs: MeetingBriefProviding` maps the response to `MeetingBriefSummary` (questions = the 3 question texts). The pill needs no change. | default |
| M7 | LLM wording | `meeting.v1` rewords only the text of the stored questions, after the deterministic brief is saved (the brief never waits for a model). Only for `full` clients with evidence, only when `FIRRA_PROCESSING_ENABLED` is on, through `firra/llm` (budget, usage, `model` and `prompt_version` stored). Checks: same count, each at most 240 characters, no new numbers or dates, no number words, no quotation marks or addresses; two attempts, then the drafts stay. A question's basis never changes. | built 2026-10-03 (owner approved spend; probe 0.05 USD) |

## Content (`meeting_brief.content`, jsonb, versioned `facts.v1`)

```json
{
  "since_last_meeting": { "records": 14, "last_held_at": "2026-09-20T10:00:00Z" },
  "open_commitments": [{ "side": "firm|client", "text": "…", "due": "2026-10-10", "evidence_ids": ["…"] }],
  "unresolved_complaints": [{ "subtype": "delay", "text": "…", "evidence_ids": ["…"] }],
  "stakeholder_changes": [{ "text": "…", "evidence_ids": ["…"] }],
  "renewal": { "date": "2026-12-01", "days_to_notice": 41 },
  "unknowns": [{ "code": "no_decision_maker", "detail": "…" }],
  "questions": [{ "text": "…", "basis": { "kind": "unknown|commitment|complaint|stakeholder|renewal", "ref": "…" } }]
}
```

`questions` has exactly 3 entries when at least 3 items exist, fewer otherwise (never invented).
Selection order: unresolved complaint → late/open commitment → stakeholder change → renewal inside
90 days → unknowns in checklist order. `metadata_only` briefs carry only counts and the unknown codes.

## Work order

| Task | What | Agent | Depends on |
|---|---|---|---|
| T45a | Migration `meeting_brief` + `meeting_brief_evidence`, RLS, pgTAP, db-types by hand (no Docker) | Claude | — |
| T45b | Worker: pure builder `firra/brief/meeting.py` + `db/meetings.py` + task `brief.meetings` + sweep hook, tests with fakes | Codex | T45a |
| T45c | API: contract + route + audit + tests (in-memory store) | Codex | T45a |
| T45d | Mac: `APIMeetingBriefs` + wiring in `AppModel` | Claude | T45c |
| T45e | LLM wording `meeting.v1` | Claude | done 2026-10-03 |

## Owner actions

None for T45a–d. Applying the migration to staging (`supabase db push`) and a connected calendar
with client events are needed before a real brief appears; neither blocks the code.

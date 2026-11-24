# Firra — Connectors

**Local v1 (2026-10-05, `docs/plans/local-v1.md`):** connectors run **in the engine on the owner's Mac**, with the owner's own credentials. v1 sources are
**Gmail, Google Calendar and Slack**. Sync is by **polling** (no Pub/Sub watch, no webhooks, no Events API). Tokens live in the Keychain and in engine memory. Outlook/
Microsoft 365, domain-wide delegation, org-wide modes and contributor connect are **not in v1** (the sections below describing them are kept for later).
Mapped-client minimization and per-client policies apply to every source. The cloud-first original is in the earlier design (not included) history.

### Local v1 rows

| Source | Auth | Sync | Notes |
|---|---|---|---|
| **Gmail** | Google **Desktop app** OAuth client, system browser, loopback redirect, PKCE; scopes `gmail.readonly`, `calendar.readonly`, `openid email`; refresh token in the Keychain | backfill 3/6/12 months; incremental by `users.history.list` every ~5 min while the Mac is awake; full resync when the history id expires | Google verification and the security assessment (CASA) are settled by research S1 (`docs/research/google-local.md`); until verified, only Google test users (refresh tokens expire after 7 days in Testing mode for all client types except basic-identity scopes, `docs/research/google-local.md`). |
| **Google Calendar** | same | sync tokens (resync on 410) | only events with client attendees |
| **Slack** | a Slack app the **owner creates in their own workspace** from a manifest Firra provides; a **user token** (read scopes only) pasted into Firra | polling: `conversations.history` of mapped channels every ~5 min, `conversations.replies` for threads whose `latest_reply` changed, daily re-scan of the last 7 days for edits and deletions; fallback and backfill: Slack export `.zip` | mapped channels (Slack Connect channels with clients; internal channels opt-in per client) and direct messages with people of a mapped client only; no files, canvases, calls or huddles; permalinks are the deep links; limits and terms settled by research L2S-01 (`docs/research/slack-local.md`) |

Always verify current provider docs (scopes, limits, API versions) before implementing a connector. Record findings at the top of the connector's module as a short docstring.

## 1. Framework

OAuth UX lives in TypeScript (`apps/api`) for the legacy cloud flow; **Local v1 connects on the Mac**
(Google Desktop OAuth via the system browser, Slack user token pasted into Firra — see the Local v1
rows above). Sync and normalization live in Python (`services/worker/firra/connectors/<provider>/`).

```python
class Connector(Protocol):
    provider: ClassVar[str]
    record_kinds: ClassVar[set[RecordKind]]

    def backfill(self, conn: Connection, window: DateRange) -> Iterator[CanonicalRecord]: ...
    def sync_incremental(self, conn: Connection) -> SyncResult: ...  # uses and updates conn.cursor
    def handle_event(self, conn: Connection, event: dict) -> list[CanonicalRecord]: ...
    def refresh_credentials(self, conn: Connection) -> None: ...
    def deep_link(self, record: CanonicalRecord) -> str | None: ...
```

- **Canonical records:** `Message`, `Call`, `TranscriptSegment`, `Task`, `Comment`, `TimeEntry`, `Invoice`, `Contract`, `CalendarEvent`, `CrmNote`. Defined once in `packages/contracts` and generated for both languages.
- Every connector: idempotent upserts on `(connection_id, external_id)`, cursor-based incremental sync, rate-limit aware client (respect `Retry-After`), token refresh, revocation detection → `connection.status = revoked` + UI prompt.
- Every connector has recorded-fixture tests (no live calls in CI) and a contract test that its output validates against canonical schemas.
- **Upload fallback** always available: `.mbox`/`.eml`/`.pst` (email), Slack export `.zip`, CSV (time entries, invoices, clients), PDF/DOCX (contracts, proposals).

## 2. Email ingestion modes

> **Local v1:** only mode 1 in a single-owner form: the **owner** connects their own mailbox on their Mac (Desktop OAuth, PKCE). Contributors, domain-wide delegation and Microsoft application permissions are **not** in v1 (deleted in F9, `docs/plans/local-v1.md` T19); the text below is kept for a later team mode.

1. **User connect (default):** each contributor connects their own mailbox via invite link (OAuth). No app access for contributors.
2. **Google Workspace domain-wide delegation:** owner/admin authorizes a service account for selected users (restricted to the scopes we need).
3. **Microsoft 365 application permissions:** admin consent in the tenant; restrict mailbox access to selected users with Exchange application access policies / RBAC for Applications.

In all modes, only threads with at least one mapped client participant are persisted (see minimization in architecture.md).

## 3. Wave 1 (P1/P4)

> **Local v1:** the Gmail, Google Calendar and Slack rows below describe the cloud-first design. The **Local v1 rows at the top of this file** replace them: polling instead of Pub/Sub watch and push channels, no Events API, no org-wide modes. Other rows are not in v1.

| Provider | Data | Auth | Sync | Notes |
|---|---|---|---|---|
| **Gmail** | email | OAuth (restricted scope for message bodies) or domain-wide delegation | history-based incremental + `users.watch` → Pub/Sub push | Watch must be renewed before expiry (renew daily). Restricted scopes require Google verification + CASA before public launch; until then limited to test/allowed users. |
| **Google Calendar** | meetings (held/cancelled/rescheduled, attendees, duration) | same Google auth | sync tokens + push channels | Only events with client attendees. |
| **Microsoft 365 Outlook** | email + calendar | OAuth delegated or application permissions (admin consent) | delta queries + change notifications (webhooks) | Subscriptions expire; renew on schedule. Multi-tenant app needs publisher verification before launch. |
| **Slack** | external (Slack Connect) and internal channels | OAuth app install (bot + optional user scopes) | Events API for real-time; `conversations.history` for backfill | Non-Marketplace commercially distributed apps are heavily rate-limited on history/replies — until Marketplace approval, backfill via admin export upload; real-time via Events API. Owner/admin chooses channels and maps each to a client; internal channels are opt-in per channel. Avoid adding a visible bot to client-shared channels when user-token read suffices. |
| **Fathom** | call transcripts, summaries | API key / OAuth per user (verify) | webhooks + polling | Text transcripts only. |
| **Fireflies** | call transcripts | API key (GraphQL) per user/team | webhooks + polling | Text transcripts only. |
| **Jira** (priority) | issues, comments, worklogs (hours), statuses | Atlassian OAuth 2.0 (3LO) | webhooks + JQL incremental | Map projects → clients. Tempo worklogs later. |
| **ClickUp** (priority) | tasks, comments, time tracking | OAuth | webhooks + incremental | Map spaces/folders/lists → clients. |

## 4. Wave 2 (P10)

| Provider | Data | Notes |
|---|---|---|
| Zoom | cloud recording transcripts (VTT) | Text only; ignore audio/video. |
| Google Meet | transcripts saved to Drive | Read transcript docs only. |
| Microsoft Teams | channel/chat messages, meeting transcripts | Some Teams message APIs are protected/metered — verify access model before building. |
| tl;dv, Otter, Gong | transcripts | Where APIs allow. |
| Asana, Linear, Monday, Productive, Teamwork, Trello | tasks, comments, hours where available | Map projects → clients. |
| Harvest, Toggl, Clockify, Everhour | time entries | Primary source for logged hours when present. |
| Xero, QuickBooks Online, FreshBooks, Stripe | invoices, payments | Payment delay drift; revenue per client. |
| HubSpot, Pipedrive, Salesforce | companies, deals, renewal dates, notes | Renewal/notice dates, client metadata. |
| Google Drive, OneDrive/SharePoint, DocuSign, PandaDoc | contracts, proposals | Extract renewal date, notice period, scope summary; proposal check input. |

## 5. External monitoring (client companies)

Matching is by client **domain** first, then company name; ambiguous matches are dropped, never guessed. Every event stores source URL and date; nothing is invented. No LinkedIn scraping; respect each source's terms.

**Free sources (P6):**
- GDELT (news mentions)
- Google News RSS per company name/domain
- Company newsroom/blog RSS where discoverable
- Public ATS job boards: Greenhouse, Lever, Ashby, Workable, SmartRecruiters (in-house role detection vs. firm's service tags)
- UK Companies House API (officer changes, filings)
- SEC EDGAR (US public companies: filings, executive changes)

**Paid adapters (behind the same `ExternalProvider` interface, enabled per plan/budget):**
- **PredictLeads** — news events, job openings (incl. deletion webhooks), financing events, technology detections; REST, webhooks, MCP. Entry pricing is low (tens of USD/month).
- **TheirStack** — multi-source job postings; free tier + paid plans.
- **Coresignal** — company, employee and job datasets (largely LinkedIn-derived — review compliance before enabling).
- Additional news APIs (e.g., Event Registry / NewsAPI.ai, NewsData.io) — evaluate coverage and terms before adding.

**Job posting intelligence (F4, P6):** an ATS board is linked to a client only when the client's own domain links to it (careers page → `boards.greenhouse.io/<slug>`, `jobs.lever.co/<slug>`, …); otherwise it is dropped. Careers pages on the client's domain are read only through schema.org `JobPosting` structured data, respecting robots.txt, through an SSRF-safe fetcher (public IPs only, size/redirect/time limits). Each posting is classified by the LLM against the workspace service catalog as `insourcing_risk` (the client hires for work the firm does), `opportunity` (new initiative or leadership that creates demand for the firm's services) or `neutral`, with a verbatim quote from the posting; postings that disappear get `removed_at`.

Cadence: daily per active client; immediate processing for webhook-based paid sources. External events feed signals (leadership change, in-house job post, funding, layoffs, M&A) and the client card.

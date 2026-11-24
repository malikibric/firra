# Firra — everything planned

> **Cloud-first draft (until 2026-10-05).** Local v1 changes the product: one owner, data on their Mac, no Vertex, no server-side mail bodies. This document is rewritten in L9-03 / L10-04 (`docs/plans/local-v1.md`); do not rely on it.


One page with every feature Firra is meant to have, whether or not it is built. It is the product
view; the build order and acceptance criteria are in `docs/roadmap.md`, the product vision in
`docs/north-star.md`, the rules every feature must obey in `CLAUDE.md` §2 and §6.

**Firra** builds a living memory of every client relationship at a service firm (15–150 people),
detects meaningful changes before people notice them, and recommends the next best action — every
claim backed by evidence. Markets: US, UK, Western Europe.

Column "Where": the milestone in `docs/roadmap.md` (A–H, "Later") and the document that specifies it.

## 1. Client intelligence (the core)

| Feature | What it does | Where |
|---|---|---|
| Client memory | Per client: people (champion, decision maker), commitments (ours and theirs), stated goals, requests, history, and the "we don't know" list (no stated goal, unknown renewal date, no reported result in 90 days) | A; `docs/architecture.md` |
| Relationship state | Stable → watch → elevated → high, from several independent signal families, with the previous state, the date it changed and the drivers | A; `docs/plans/operator-audit.md` |
| Evidence for every claim | Each claim links to a verbatim quote (3–25 words), where it came from, who wrote it and when, and a deep link to the original; a claim whose quote cannot be verified is dropped | rule 2; `docs/architecture.md` |
| Risk signals | Reply-latency drift, champion silence or departure, new senior stakeholder, cancelled or rescheduled meetings, repeated complaints (by subtype), executive escalation, tone decline of the client's written text (per account, never per person), reduced engagement (also for metadata-only clients), late commitments, goal versus reported results, drop in request volume, renewal and notice windows, first 90 days without a result | A; `docs/architecture.md` §4 |
| Latent demand | The same ask or problem recurring over time (at least 3 mentions in 2 threads within 60 days) becomes an expansion opportunity (matches a firm service) or an unmet need; counts come from SQL, the model only names the topic | A; F2 |
| Recommended play | One suggested action per client, including "do not sell yet, fix X first" | A; `docs/plans/operator-audit.md` |
| Advise Me | On demand, one grounded recommendation per client: fix first, opportunity, renewal and pricing, or maintain; cached, rate limited, audited | F5 |
| Churn Replay | On the firm's own history, shows week by week when a client moved from stable to watch to elevated, how many days before notice or churn Firra would have warned, the exact messages behind each change, and the false alarms on clients who stayed | B |
| Closed loop | Every surfaced item can be rated useful / already knew / wrong / not now; outcomes (renewed, churned, expanded, downgrade, price increase, referral) are recorded; precision, lead time and usefulness are measured per signal family and play | C |

## 2. The Mac app (the only UI)

| Feature | What it does |
|---|---|
| Menu bar | "N clients need attention", the top items, native notifications for clients that got worse |
| Today / what changed | Who needs attention now and what changed since the last look |
| Clients and client detail | State, drivers with their quotes, stakeholders and champion, commitments, goals, recurring asks, unknowns, play, history, recent events, outcomes |
| Churn Replay | Runs, results and each client's week-by-week story |
| Decisions | Ranked suggestions with evidence and one-tap feedback |
| Setup | Clients, their email domains and data policies, service catalog, connected mailboxes, team members, contributors, mail import (mbox, eml), audit log |
| Mail on this Mac | The firm's mail lives in an encrypted vault on each user's Mac; the server keeps only derived facts and short quotes; "Read the whole mail" behind a quote, "Fetch my mail again" after a lost vault |
| Contributor view | A team member connects their own mailbox, sees what is collected and can disconnect; sees no client data |
| Connect AI tools | Create and revoke personal access tokens for the MCP server |
| Distribution | A DMG signed with a local certificate; no Apple Developer account, notarization or auto-update planned for now |

Specified in `docs/plans/d-mac-app.md`.

## 3. Routines and delivery (E, G)

| Feature | What it does |
|---|---|
| Morning client scan | Lists the clients whose state changed or crossed a threshold |
| Pre-meeting brief (F1) | Before each client meeting, for owner and admin: what changed since the last meeting, open commitments per side, unresolved complaints, stakeholder changes, tone shift, scope and pricing questions, recent company news and job postings, what we don't know, and three questions to ask, each tied to the item behind it |
| Commitment watch | Raises commitments that are due or late |
| Weekly founder review | The week in three to five ranked decision cards |
| Expansion scout, risk watch | Opportunity and risk routines on top of the same assessments |
| Importance and budget | FYI stays in Firra, "worth attention" joins the brief, "important" becomes a notification; at most about three important notifications per workspace per week; an item marked "not now" is not raised again without new evidence |
| Email and Slack delivery | Briefs and high-severity alerts by email (Resend) and Slack DM, only where design partners ask (G) |

## 4. MCP server (F)

Firra's intelligence inside the founder's Claude or ChatGPT: read-only, policy-aware, audited, every
answer with evidence ids and links. Examples: "Which clients need my attention?", "Prepare me for
Acme", "Why is Acme on watch?", "What do we still owe Delta?", "What is growing at Epsilon?".
Personal access tokens first, OAuth 2.1 for one-click connectors next, directory submission later.
Specified in `docs/plans/f-mcp.md` and `docs/research/mcp.md`.

## 5. Data sources

| Group | Sources | When |
|---|---|---|
| Mail and calendar | Gmail, Google Calendar, Outlook and Outlook Calendar (user connect, Google domain-wide delegation, Microsoft application permissions), mail export upload | first |
| Chat and calls | Slack (external and internal channels, mapped to clients), Fathom, Fireflies, Zoom, Google Meet, Microsoft Teams, tl;dv, Otter, Gong (text and transcripts only) | by hypothesis (Later) |
| Projects and time | Jira, ClickUp, Asana, Linear, Monday, Trello, Productive, Teamwork; Harvest, Toggl, Clockify, Everhour | by hypothesis |
| Money and CRM | Xero, QuickBooks, FreshBooks, Stripe; HubSpot, Pipedrive, Salesforce | by hypothesis |
| Contracts | Upload and Drive, OneDrive, SharePoint, DocuSign, PandaDoc: renewal date, notice period, scope | by hypothesis |
| The clients' own companies (F4) | News (GDELT, Google News RSS), leadership changes and filings (UK Companies House, SEC EDGAR), public job boards linked from the client's own domain (Greenhouse, Lever, Ashby, Workable, SmartRecruiters) and schema.org job data on careers pages; postings are classified as insourcing risk, opportunity or neutral against the firm's service catalog; paid adapters (PredictLeads, TheirStack) behind the same interface; never LinkedIn scraping | P6 / Later |

Specified in `docs/connectors.md`.

## 6. Growth, profit and learning (later layers)

| Feature | What it does |
|---|---|
| Growth | Referral and case-study moments (explicit praise of results with healthy signals), expansion signals, an ideal-client profile from the firm's own data (which clients stay longest and have the best margin) |
| Real margin | Revenue minus logged hours at role cost minus estimated shadow work (from message volume and meeting time), shown as a range |
| Revenue at risk | Next 90 days, from MRR and the severity of open signals |
| Renewal and price-increase pack | Delivered work, the client's own stated results, margin, relationship state and a suggested increase range |
| Proposal check | Compares a new proposal's estimates with actual hours of similar past engagements |
| Package discovery | Clusters recurring requests across clients, names the clusters and shows frequency and hours spent |
| Decision log and scoreboard | What was suggested, what was done, what happened |

Profit and pricing numbers are deterministic math, never model output.

## 7. Platform

| Area | What it includes |
|---|---|
| Teams and roles | Owner and admin with full access; contributors who only connect and disconnect their own sources; invites by code; every read of client data audited |
| Data policy per client | full, metadata only or excluded, enforced at ingest and at every read path (screens, briefs, MCP) |
| Minimization | Only mail involving a mapped client is kept; everything else is discarded on arrival |
| Privacy and retention | Mail bodies are deleted from the server once processed and delivered to the owner's Mac (72 hours at most undelivered, 7 days at most overall); quotes are kept at 25 words or fewer; hard delete of a client, a connection, a contributor's data or the whole workspace |
| Isolation and security | Row-level security on every tenant table, encrypted OAuth tokens (per-workspace keys), webhook signature checks, EU-only processing (Frankfurt, Vertex AI EU), prompt-injection safe handling of all ingested text |
| Cost control | Per-workspace monthly model budget with alerts at 50, 80 and 100 percent |
| Billing | Subscription, plan limits and entitlements (Lemon Squeezy behind a provider interface), customer portal |
| Account lifecycle | Workspace export and complete deletion, legal pages (privacy, DPA, sub-processors) |
| Launch gates | Google OAuth verification and CASA for restricted Gmail scopes, Microsoft publisher verification, security review, load test, backup and restore drill, runbooks |
| Localization | Mac app English first with string catalogs; generated text in the recipient's locale, quotes in their original language |

## 8. Deliberately not built

A CRM, a generic AI chat, a dashboard of scores, an email summarizer, a sentiment product, employee
monitoring or ranking, an autonomous outreach agent, anything that sends to a firm's clients without
a person, voice, video or face analysis, "find unbilled money", invoice recovery or change-order
generation, LinkedIn scraping, markets outside the US, the UK and Western Europe.

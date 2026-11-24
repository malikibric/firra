# Firra — North star

Source of truth for *what* Firra is. Adopted 2026-09-30 (direction reset, Mac app + MCP) and **updated 2026-10-05 (Local v1: Firra runs
on the owner's Mac)**. `CLAUDE.md` holds the rules; `docs/plans/local-v1.md` the order of work. Earlier versions: the earlier design (not included).

## What Firra is

An always-on client relationship intelligence system for service businesses (agencies, consultancies, software/IT outsourcing firms;
founder-led firms of about 5–30 people first), **running on the founder's own Mac**.

> Firra builds a living memory of every client relationship, detects meaningful changes before humans notice them, and recommends the
> next best action with evidence — and the founder's data never leaves their Mac.

**Connect once → Firra continuously understands the client → the user's AI can use both Firra intelligence and safe,
client-scoped source context through Firra MCP.** One Gmail, Calendar and Slack connection on the Mac feeds one local database; the
engine turns it into judgement, and the MCP server exposes that judgement plus bounded, policy-aware excerpts of one client's sources
(`docs/plans/local/source-layer.md`). Firra is not a generic mail or Slack gateway.

The intelligence is the product. The Mac app, the MCP server, notifications and any later delivery channel are interfaces to it. The
user's own AI (Claude, ChatGPT, Claude Code, Codex, Cursor) is both Firra's extraction engine and its conversational surface;
**Firra is the memory, the verifier and the judge**: it checks every claim against the source and decides states, plays and importance
deterministically.

**Not** a CRM, a generic AI chat, a dashboard of scores, an email summarizer, a sentiment product, employee monitoring, or an
autonomous outreach agent.

## The one question everything serves

> Can Firra reliably tell a founder something important about a client that they did not know, or would have noticed too late?

The first proof ("holy shit" moment) is **Churn Replay**: the founder connects historical mail and Slack, picks a lost (or active)
client, and Firra shows — without hindsight — when the relationship moved from stable to watch to elevated, which independent signal
families changed, the exact messages and events behind it, and the play it would have recommended.

## Surfaces

| Surface | Role |
|---|---|
| **Native macOS app** (Swift/SwiftUI, menu bar, notifications) | Primary product: Today / What changed, Clients, Client detail, Churn Replay, Routines, onboarding, Settings, Connect AI tools |
| **MCP server** (in the engine) | Two layers inside the founder's AI: Firra's intelligence, and client-scoped source excerpts (off until switched on). Local stdio for tools on the same Mac; a public link (relay, OAuth) for claude.ai and ChatGPT. Read-mostly, audited. |
| Email / Slack delivery | Later, only where design partners ask |

There is no browser dashboard. Firra's cloud is a **control plane** (accounts, devices, OAuth, relay, downloads); it never holds client data.

## Architecture

```
 claude.ai / ChatGPT ──HTTPS+OAuth──▶ relay (stores nothing)                 Claude Code / Codex / Cursor ──stdio──┐
                                           │ outbound WebSocket from the Mac                                     │
 ┌─────────────────────────────────────────▼─────────────────── the owner's Mac ───────────────────────────────────▼─┐
 │ Mac app (Swift)  ──127.0.0.1──▶  Firra engine (Python): sources · clean · resolve · AI extraction · VERIFY ·        │
 │                                   memory · signals · assessment · plays · replay · routines · read models · MCP    │
 │                                   Local Postgres: facts, evidence, history, feedback, outcomes, audit (+ encrypted │
 │                                   mail and chat text)                                                              │
 └──────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
        sources: Gmail, Google Calendar, Slack (owner's own accounts)     AI: the owner's own signed-in tool
```

Rules that keep interfaces thin:
1. **Every judgement is computed and persisted by the engine** (states, drivers, plays, changes, importance, replay summaries).
   Interfaces and the user's AI never recompute or re-rank intelligence.
2. **One read layer.** The engine's read models serve the Mac app and MCP; an MCP tool is a thin adapter over a read model.
3. **Contracts first.** Response shapes are JSON Schemas in `packages/contracts` (→ zod, Pydantic, OpenAPI). A breaking change needs a new version.
4. **The AI proposes, Firra verifies.** Quotes must exist verbatim in the source; schema, policy and client status are checked; anything else is dropped.
5. **LLMs word, they do not decide.** Deterministic rules choose states and plays; the owner's AI phrases text from facts; numbers, dates and quotes come from Firra.
6. **Reads are policy-aware and audited**; writes (feedback, outcomes, extractions) pass deterministic validation.

## The moat

A continuously learning client relationship memory, kept on the owner's Mac: raw interactions → verified facts → temporal memory →
stakeholder graph → change detection → signals → assessment → play → founder action → real outcome → learning. What makes Firra hard
to replace is the per-firm history: relationships, stakeholders, prior warnings, what the founder acted on, which warnings were useful
or wrong, and how each client ended. Every surface captures feedback and outcomes. Cross-firm learning is possible only through an
explicit, content-free, opt-in metrics export.

## Principles (in priority order)

1. Intelligence quality 2. Trust and evidence 3. Temporal correctness (no hindsight) 4. Real founder usefulness 5. Feedback and
outcome learning 6. Privacy and security (data stays local) 7. Native Mac quality 8. MCP distribution 9. More sources 10. More features.

Fewer, correct, actionable observations beat many noisy ones. Every milestone is judged by evals, not by features shipped.

## Intentionally not built

Unbilled-money detection, invoice recovery, change orders · voice, video, face or biometric emotion analysis · rating or scoring the
firm's employees · auto-sending anything to clients (drafts only) · LinkedIn scraping · a browser dashboard, generic chat UI, health-score
gamification, chart walls, an automation marketplace · teams and shared memory (not in v1) · a model that Firra pays for · dozens of
integrations "because competitors have them".

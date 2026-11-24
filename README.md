<div align="center">

# Firra

### Your clients are quietly telling you they're leaving. Firra reads the signs.

**Local-first client relationship intelligence for founder-led service firms.**
A living memory of every client, built from your own Gmail, Calendar and Slack. It flags what changed *before* you'd notice, and it tells you the next move, with the receipts.

**Runs on your Mac. Your client data never leaves it.**

[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
![Platform: macOS 14+](https://img.shields.io/badge/platform-macOS%2014%2B-lightgrey)
![MCP](https://img.shields.io/badge/MCP-server-8A2BE2)
![Status: early](https://img.shields.io/badge/status-early%20%F0%9F%9A%A7-orange)

[The idea](#the-idea) · [Churn Replay](#churn-replay-the-holy-shit-moment) · [Use it from your AI](#use-it-from-your-ai) · [How it works](#how-it-works) · [Quick start](#quick-start) · [Contributing](#contributing)

</div>

---

## The problem

Every agency and consultancy founder has lived this:

> *"They were fine in March. In June they cancelled. Looking back, the signs were everywhere."*

The champion replied slower. A new VP showed up on the thread. Two meetings got rescheduled. Someone mentioned "budget review" once. Nobody connected the dots, because the dots are spread across 4,000 emails and a Slack workspace, and you were busy doing the actual work.

CRMs don't catch this. They only know what you remembered to type in. Dashboards of "health scores" don't catch it either, and they rarely say *why*.

## The idea

Firra builds a **living memory of every client relationship** from the tools you already use, then does three things:

1. **Remembers.** Who the champion is, who decides, what you promised, what they promised, what they asked for twice, and what you *don't* know yet.
2. **Notices.** Relationship state moves `stable → watch → elevated → high` when *several independent signal families* change at once: reply-latency drift, champion silence, a new senior stakeholder, cancelled meetings, repeated complaints, escalation.
3. **Tells you what to do.** One recommended play per client, including the unglamorous ones like *"don't pitch the upsell yet, fix the reporting issue first."*

And the rule that makes it trustworthy:

> **Every claim links to a verbatim quote from the source. If Firra can't find the quote, the claim is dropped.**

No vibes. No unexplained scores. Evidence or silence.

## Churn Replay: the "holy shit" moment

Connect your historical mail and Slack. Pick a client you lost. Firra replays the relationship **without hindsight**, week by week:

- when it moved from stable to watch to elevated,
- which independent signals changed,
- the exact messages behind each move,
- the play it would have recommended,
- and how many days before the churn you'd have been warned.

It also shows the *false alarms* on clients who stayed, because a smoke detector that always screams is worthless. Precision and lead time are measured, not claimed.

## Use it from your AI

Firra ships a native Mac app and an **MCP server**, so the AI you already use becomes your client-intelligence interface:

```
You:     Who should I worry about this week?
Claude:  Two clients moved up. Umber went to "watch": their decision maker
         hasn't written in 5 weeks and the thread shrank from 4 people to 1.
         [evidence: 2 quotes, 3 events]. Recommended play: a direct check-in
         from you, not the account manager. …
```

Works with Claude, ChatGPT, Claude Code, Codex and Cursor:

- **Local stdio** for tools on the same Mac.
- **Public link** (OAuth + a relay that stores nothing) for claude.ai and ChatGPT.
- **Two layers**: Firra's *judgements* (states, risks, opportunities, commitments, meeting prep), and *client-scoped source excerpts* (off until you switch them on, never inbox-wide, never workspace-wide).

An agent skill in [`skills/firra`](skills/firra) teaches your AI how to use it: which tool for which question, how to cite evidence, what never to say.

## Privacy is the architecture, not a feature

| | |
|---|---|
| **Where does my data live?** | In a local Postgres on your Mac. Mail and chat text are encrypted at rest. |
| **What does the cloud see?** | Nothing about your clients. The small cloud part is a control plane: accounts, OAuth, a relay that forwards bytes, downloads. |
| **Whose AI reads my email?** | Yours. Firra uses your own signed-in AI tool for extraction. There is no Firra-hosted model in the loop. |
| **Can the AI make stuff up?** | It proposes, Firra verifies. Quotes must exist verbatim in the source; schema, policy and client status are checked; anything else is dropped. |
| **Who decides states and plays?** | Deterministic rules, not an LLM. Models *word* things; they never *decide* them. |
| **Is it audited?** | Reads are policy-aware and logged. Writes pass deterministic validation. |

## How it works

```
 claude.ai / ChatGPT ──HTTPS+OAuth──▶ relay (stores nothing)          Claude Code / Codex / Cursor ──stdio──┐
                                           │ outbound WebSocket from your Mac                              │
 ┌─────────────────────────────────────────▼──────────────────── your Mac ──────────────────────────────────▼─┐
 │ Mac app (Swift)  ──127.0.0.1──▶  Firra engine (Python): sources · clean · resolve · AI extraction · VERIFY ·  │
 │                                   memory · signals · assessment · plays · replay · routines · MCP            │
 │                                   Local Postgres: facts, evidence, history, feedback, outcomes, audit        │
 └──────────────────────────────────────────────────────────────────────────────────────────────────────────┘
        sources: Gmail, Google Calendar, Slack (your own accounts)          AI: your own signed-in tool
```

The pipeline: **raw interactions → verified facts → temporal memory → stakeholder graph → change detection → signals → assessment → play → your action → real outcome → learning.**

Every surface captures feedback (*useful / already knew / wrong / not now*) and real outcomes (*renewed, churned, expanded…*), so the system learns what's actually useful for *your* firm.

**What Firra is not:** a CRM, a generic AI chat, a dashboard of scores, an email summarizer, a sentiment product, employee monitoring, or an autonomous outreach agent.

## What's in the box

| Path | What |
|---|---|
| [`apps/mac`](apps/mac) | Native macOS app (Swift/SwiftUI): menu bar, Today / What changed, clients, Churn Replay, routines, onboarding |
| [`services/worker`](services/worker) | The engine (Python, package `firra`): connectors, extraction, verification, signals, assessment, replay, MCP tools |
| [`apps/api`](apps/api) | Small TypeScript control plane: MCP relay, OAuth, device registration |
| [`packages/contracts`](packages/contracts) | JSON Schemas → generated zod + Pydantic + OpenAPI |
| [`supabase/migrations`](supabase/migrations) | Forward-only SQL + pgTAP tests |
| [`skills/firra`](skills/firra) | Agent skill for using Firra's MCP well |
| [`infra/terraform`](infra/terraform) | The small cloud only |
| [`docs`](docs) | North star, architecture, connectors, roadmap, runbooks, research |

The repo ships a fully **synthetic** dataset ("Northbeam Growth": a fictional agency with 15 clients, 12 months, ~700 messages, planted churn scenarios and benign controls) so you can develop and evaluate without touching real customer data. See [`docs/runbooks/synthetic-data.md`](docs/runbooks/synthetic-data.md).

## Quick start

Requirements: macOS 14+, Node 24, pnpm 10, [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/malikibric/fira.git
cd fira

pnpm install
uv sync --all-packages
cp .env.example .env

# start the bundled local Postgres, run the checks
eval "$(scripts/local-db.sh start)"
scripts/check.sh worker        # ruff, mypy, pytest
scripts/check.sh sql           # pgTAP + integration tests
apps/mac/scripts/test.sh       # Mac app tests
```

Full setup, the engine, and the bundled database: [`docs/runbooks/local-dev.md`](docs/runbooks/local-dev.md).

## Status

Firra is **early**. The engine, verification layer, signal rules, replay, MCP server and Mac app exist and are tested against synthetic data. Expect rough edges, moving schemas and a lot of opinions. Read the [roadmap](docs/roadmap.md) and [north star](docs/north-star.md) to see where it's going and what is deliberately *not* being built.

## Contributing

Firra is Apache-2.0 and built in the open. Good places to start:

- 🐛 Try it, break it, open an issue.
- 🔌 Add or harden a connector ([`docs/connectors.md`](docs/connectors.md)).
- 🧪 Write a nasty synthetic scenario the signals miss. Oracle tests are the best contribution here.
- 🌍 Improve non-English signal quality.

Read [`CONTRIBUTING.md`](CONTRIBUTING.md) first. Security issues: [`SECURITY.md`](SECURITY.md), please don't open a public issue.

## Star it if you've ever lost a client you should have seen leaving ⭐

## License

[Apache License 2.0](LICENSE)

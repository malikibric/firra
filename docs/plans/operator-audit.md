# Firra operator audit (2026-09-29)

Audit of Firra against the "AI client operator / AI VP of Client Success" concepts. Scope: `main`
at `511ecdc` plus open PR #34 (account memory) and PR #35 (policy reaches derived data). Read:
migrations, worker pipeline, signals engine, prompts, evals, Northbeam spec, web routes, plans.
Nothing executed, no model spend. The owner accepted the recommendations on 2026-09-29; the
resulting plan is [`p2b-operator.md`](p2b-operator.md).

## Bottom line

1. **Firra today is a well-secured evidence ledger with one LLM extraction step, not yet an
   intelligence product.** A founder who connects Gmail gets nothing back: the web app has setup,
   clients, connections, contributors, members, services and audit pages; 2 of 14 signal rules are
   live; decision cards, briefs and Advise Me have tables or plans but no generator; extraction has
   never run on the eval corpus (the Northbeam corpus is not generated).
2. **The architecture points the right way:** deterministic rules, verbatim-verified evidence,
   `rule_version`/`prompt_version`, and an `as_of` reference time that already caps churned clients
   at `churned_at` — the seed of Churn Replay.
3. **Three structural gaps block the operator:** signals are stored as current state only (the
   upsert overwrites severity), so Firra cannot say "Medium to High over 18 days"; nothing combines
   weak signals per account, so "B accounts" are invisible by construction; there are no feedback or
   outcome tables, so the system can neither learn nor be measured on outcomes.
4. **The biggest product risk is sequencing:** the roadmap shipped a dashboard (P3) four milestones
   before the operator loop (P7 weekly brief), and added Slack, profit and external monitoring
   before any founder reacted to a Firra recommendation.
5. **Recommendation:** keep the pipeline. Now: signal history, an account assessment layer,
   feedback and outcome capture, leak-free `as_of`, timing-aware evals. Next: Churn Replay and a
   weekly account review as the first things design partners see. Defer MCP, ICP report, package
   discovery and any multi-agent design.

## 1. Current state

| # | Concept | Status | In one line |
|---|---|---|---|
| 1 | AI VP of Client Success / Client Operator | PARTIAL | Designed as an operator (rules 6–7, decision cards, Advise Me gate); built as ingest, extract, verify and 2 rules. No user-facing intelligence. |
| 2 | Truth → Prediction → Recommendation → Action | PARTIAL | V1 in the backend only; V2 absent; V3 a table (`decision_card`); V4 excluded by rule 6 (right for now). |
| 3 | Stealth churn / relationship silence | PARTIAL | `tone_decline`, `reduced_engagement` compare with the account's own baseline; 12 rules disabled; no trajectory; email-only and blind to mailbox coverage. |
| 4 | B accounts: hidden risk and expansion | MISSING | Every rule fires on its own threshold; nothing combines sub-threshold signals; expansion = repeated asks only (T10). |
| 5 | Specialized components vs one agent | PARTIAL; multi-agent NOT WORTH DOING | Already deterministic specialists + one LLM call per email; missing account-level assessment and a claim-level skeptic. |
| 6 | Trust, evidence, confidence, control | PARTIAL | Data layer is the strongest part of the codebase; nothing surfaced; no confidence concept; card text mixes fact and advice; a verified quote does not prove the claim. |
| 8 | Moat | PARTIAL | Evidence ledger, service taxonomy, eval harness exist; outcome labels, calibration, cross-channel data, decision log do not. |
| 9 | Lost clients / Churn Replay | MISSING | No feature, but a strong seed: facts load "as of" a date, churned clients capped at `churned_at`, 3 churned Northbeam clients with expected signals. |
| 14 | Measure by outcomes | PARTIAL | Scorer for citations, extraction, signals, needs, FP cards, cost exists and never ran; no lead time, feedback, outcome or business metrics. |
| 16 | Closed loop | MISSING | Only the first links exist; no outcome table, no feedback model; weights hand-set in YAML. |

### Evidence per concept

**1 · AI VP of Client Success.** Exists: operator framing (`CLAUDE.md` §2 rules 6–7); pipeline
`firra/ingest/pipeline.py` → `firra/policy/minimize.py` → `firra/clean/email.py` →
`firra/extract/process.py` (prompt `extract.v1`, 10 kinds) → `firra/verify/quotes.py` → `evidence`;
account memory in PR #34 (`firra/memory/rules.py`, `unknowns.py`, `firra/db/memory.py`); signals
engine with 2 of 14 rules; tables `decision_card`, `latent_need` without producers. Missing: card
generator (T11/T12 blocked), meeting brief, Advise Me, weekly brief (`brief`, `meeting_brief`,
`advice` tables not created); the web app has no client page, signals or evidence view.

**2 · Stages.** V1: extraction + evidence built, memory in review, 2/14 rules, no UI, never run on
the eval corpus. V2: signals detect current state; the only planned forecast (P5 revenue at risk =
Σ MRR × risk weight) multiplies by invented weights. V3: `decision_card` + the Advise Me gate on
paper. V4: drafts only (rule 6).

**3 · Stealth churn.**

| Behavior | Status | Where / why |
|---|---|---|
| Communication frequency drops | Built | `reduced_engagement`: 30 d ≤ 0.5 × monthly average of days 31–120 |
| Reply latency increases | Planned | `champion_latency_drift`, champion only |
| Executive sponsor disappears | Partial | `champion_departure` needs an explicit departure; a silent decision maker is only an F1 unknown |
| Meetings cancelled/rescheduled | Planned | `meeting_cancellations`, absolute share, not baseline-relative |
| Fewer stakeholders participate | Missing | data exists (`participants`, `attendee_count`), no rule |
| Client initiates fewer conversations | Missing | derivable from each thread's first message |
| Decline in positive language | Partial | only a rise in negative share is measured |
| Repeated scope/budget concerns | Missing | `renewal_or_budget_talk` has no direction |
| Relationship turns transactional | Missing | speculative |
| Invoice/payment friction | Missing | no invoices until P5 |

Signal storage is current-state only (`firra/db/signals.py` `_UPSERT … do update set severity = …`;
`_RESOLVE` closes what a run did not produce) — no trajectory, and signals near a threshold
flicker. The loader counts only email (`r.kind = 'email'`) and does not know when a mailbox was
connected, revoked or errored.

**4 · B accounts.** `engine.evaluate` runs rules independently; D14 ranks single signals;
sub-threshold observations are not stored. Expansion covers only the repeated ask (T10); no
extraction kind for initiatives, `renewal_or_budget_talk` lumps growth with cuts,
`new_senior_stakeholder` is risk-only. Northbeam's planted scenarios are all obvious.

**5 · Specialized components.** Risk Analyst = rule functions; Relationship Mapper =
`firra/memory/rules.py`; Commitment Tracker = `commitment` + `late_commitment`; Expansion Scout =
latent needs (T10); Commercial Analyst = P5; Research = P6; Account Strategist = cards/Advise Me (not
built); Skeptic = `verify`, `extract/items.py`, `engine._problem` (citations and fields, never
conclusions). Separate LLM agents per role would add cost and non-determinism with no eval showing a
gain. Two components would materially help: a deterministic account assessor and a claim-support
check for high-impact items.

**6 · Trust.** Strong: verbatim verification (dropped, never repaired); deferred
`require_evidence` triggers; composite FKs `(workspace_id, evidence_id, client_id)`; tone,
escalation and delivery-complaint signals must be evidence-based; metadata signals store `inputs`;
`rule_version`/`prompt_version`/`model`; `audit_log`; prompt-injection tags and eval check. Gaps:
nothing reaches a user; no confidence or strength concept; `payload.text` is an unverified
paraphrase next to a verified quote; `decision_card.body` mixes fact, interpretation and advice; no
"why this might be wrong" checks. Firra takes no client-facing action; the only destructive paths
(policy change, purge) are audited.

**8 · Moat.** Cross-channel data: email + calendar only (medium). Account memory: high only with
outcomes. Service taxonomy: medium, copyable. Calibration and outcome learning: none (highest
potential). Eval infrastructure: internal velocity. Decision log: none. Commodity today:
summarizing email, tone detection, extracting asks and commitments, health views, meeting-prep
summaries. Constraint: cross-customer learning may only use derived, non-identifying features
(signal types, timings, outcomes) and needs explicit DPA wording.

**9 · Churn Replay.** Seed: `SignalRepo.load_facts` caps `ref` at `churned_at` and filters
`occurred_at <= ref`; `signals.evaluate` and `memory.rebuild` take `as_of`; rule 1 allows churned
clients for calibration; `redfern`, `sable`, `tamsin` in Northbeam. Blockers: one `as_of` per run
overwriting live rows; time leakage (`is_champion`, `is_decision_maker`, commitment status are
current values; people first seen after `ref` are loaded); the scorer ignores when a signal fired;
12-month backfill and 15-day purge mean extraction must happen at onboarding.

**14 · Metrics.** Tracked: `llm_usage`, `llm_budget_event`, job counters; eval scorer (citation
validity, extraction P/R by kind, injection, signal P/R by type, needs exact counts, FP cards,
verify drops, cost) — never run. Not tracked: lead time, flicker, benign-drift false alarms,
feedback, acceptance, action taken, outcomes, GRR/NRR, surprise churn.

**16 · Closed loop.** Interaction → extraction: built (email). History: partial. Analysis: 2 rules.
Evidence presented: data only. Recommendation, human verdict: schema only. Outcome: specified, not
migrated. Learning: none. In design Firra generates insights; in code it stores evidence.

## 2. Gap analysis

| # | Current | Desired | Gap | Why it matters |
|---|---|---|---|---|
| 1 | Ledger + extraction, no output | Monday: the 3–5 accounts that changed, why, evidence, what's next, the move; meeting prep; memory of decisions and outcomes | Assessment layer, cards, weekly delivery, feedback/outcomes, drill-down page | A founder pays to stop being surprised by clients |
| 2 | V1 backend only | V2 as trajectory + deadline + precedent; V3 as rule-chosen plays worded by the LLM; V4 later as drafts | Signal history, deadlines as drivers, precedent from replay, play library | "Health 61" says nothing; a dated consequence and a move do. 3–5 losses per firm per year cannot calibrate probabilities |
| 3 | 2 baseline rules, history overwritten, email-only, coverage-blind | Weekly metrics vs the account's own normal, with trajectory and coverage correction | 5 metadata rules, budget/scope direction, coverage guard, history | Silent churn shows in behavior first; a false "engagement down 70 %" from an expired mailbox token ends a pilot |
| 4 | Independent rules, obvious scenarios | Compound watch/elevated from weak signals; expansion hypotheses | Watch bands, combination rules, `initiative` kind, B-account scenarios | Where the founder gets news, not confirmation ("already knew" label measures it) |
| 5 | Right shape | + account assessor, claim-support check, one wording call per account | Those components | Each component gets its own metric |
| 6 | Strong data layer, no surface | Facts / interpretation / move separated; strength with reasons; caveats; one-click feedback | Structured cards, strength tiers, caveats, evidence drawer, feedback | A wrong alert without a visible reason ends a pilot |
| 8 | Foundations | Outcome-labelled history → per-workspace calibration → consented cross-workspace priors | Outcome, feedback, history tables; replay; DPA wording | Without outcomes Firra is an approximable summarizer |
| 9 | Seed only | Onboarding replay of lost clients next to retained-client false alarms | Assessment history per `as_of`, leak-free facts, lead-time scoring, controls, replay page | Day-one value and the only test of signal validity on real behavior |
| 14 | Correctness metrics coded, never run | Layered framework, outcome metrics from the product | Timing in scorer, Northbeam v3, feedback/outcome tables, calibration report | Otherwise only vanity counts exist |
| 16 | Truth half built | Interaction → … → outcome → calibration, learning = measured precision/lead time change | Feedback, outcomes, history, calibration | "Gets smarter" must be a number that goes up |

### Deep dive: Churn Replay

**Verdict:** a strong candidate for the killer feature (onboarding value moment, calibration and
sales demo at once), credible only if it reports misses and false alarms.

- **Data:** clients lost in the last 9–12 months (≥ 90 days of history inside the 12-month
  backfill), `churned_at`, plus "notice received on" and "when did you first sense trouble?";
  retained clients as controls; mailboxes of the people who handled them (domain-wide delegation
  reaches suspended accounts; else `.mbox`/`.pst` upload); calendars. Extraction runs at onboarding
  (15-day purge) within the first-month budget.
- **Logic:** freeze `rule_version` and the assessment version; weekly assessments for every client
  using only facts dated ≤ `as_of`; detection = first elevated state holding ≥ 2 consecutive weeks;
  lead time = notice (or `churned_at`) − detection; retained clients give the false-alarm rate; per
  family "before X of N losses, in Y of M retained clients" with n visible; misses listed with the
  reason visible in the data.
- **UX:** onboarding step "which clients did you lose in the last 12 months?"; one replay page per
  lost client with checkpoints T-90/-60/-30/-14/notice, drivers with quotes or numbers, the
  founder's "we knew" marker; summary with hits, lead time, false alarms, misses; "active clients
  that match a pre-loss pattern today"; the same page on Northbeam as the sales demo.
- **Limitations:** small n (descriptive, no probabilities); lost mailboxes; channel blind spots;
  many losses are external causes and must not be forced into a story.
- **Against hindsight bias:** versions frozen before the run, no tuning on reported clients
  (leave-one-out later), leak-free facts, the LLM never sees outcomes or later messages, templated
  replay text, retained controls always shown, persistence ≥ 2 weeks, wording "showed / was
  elevated", never "would have saved".
- **Influence on active clients:** precedent lines and priority on cards; thresholds change only
  through owner-approved config changes measured in evals; cross-workspace priors later, with
  consent. Rule 1: replay is calibration; active clients remain the output.

### Deep dive: evaluation framework

Every layer has its own metric and gate. Real-world labels can only come from users' clicks and
recorded outcomes (`CLAUDE.md` §14.6), so the feedback UI is eval infrastructure.

| Layer | Question | Metrics | Source | Use |
|---|---|---|---|---|
| L0 Evidence | Are quotes real? | Citation validity 100 %, injection 0 | Northbeam; periodic re-verify | Hard gate |
| L1 Extraction | Did we read it right? | P/R per kind; claim-support accuracy for high-impact kinds | Northbeam; "wrong" flags | Gate on prompt change |
| L2 Signals | Do rules fire as specified? | Oracle 100 % | `oracle.yaml` | CI |
| L3 Assessment | Does the state precede outcomes? | Losses caught ≥ 30 d ahead; median lead; false alarms per 10 clients per month; flicker | Northbeam v3; replay | Gate on config change |
| L4 Recommendation | Right and useful move? | Play accuracy; useful / already knew / wrong / not now; novelty | Northbeam; product | Weekly |
| L5 Action | Did the founder act? | Action-taken rate, time to action | Product | Weekly |
| L6 Outcome | Did it matter? | Surprise-churn rate; GRR, NRR; confirmed expansion | `outcome` | Quarterly |
| Cost | Affordable? | USD per active client per month, per eval run | `llm_usage` | Budget gate |

North star: surprise-churn rate, guarded by false alarms per 10 clients per month, plus weekly
"useful and not already known" cards. Never report messages analyzed, insights generated or
summaries produced. Northbeam was written to the rule catalog, so high recall on it proves the
implementation, not predictive validity — that comes only from replay on real history and live
outcomes.

## 3. Product critique

| Risk | Level | Why | Counter |
|---|---|---|---|
| Another AI dashboard | High | P3 was portfolio + client page; the weekly brief was P7 after P4–P6 | Brief first; the client page is the drill-down |
| Another summarization layer | Medium | "Since the last meeting" and the card summary line can become recaps | Keep F1 to deltas, open items, unknowns, questions |
| Another health-score product | Medium, rising | `decision_card.score` and P5 revenue-at-risk weights would surface as precise numbers | Scores internal; show states and drivers; plain MRR sums |
| Over-engineered multi-agent | Low for LLM agents; high for breadth | 5 locales, MCP, 25+ connectors, prod Terraform, 3-agent team — and no founder has reacted to a recommendation | Freeze breadth until the loop has design-partner feedback |

Also: obvious-signal bias in planted scenarios; circular evaluation; channel and coverage
blindness; quote verified ≠ claim verified; flicker from `_RESOLVE`; tone as a headline (most
commodity, most sensitive); `request.text` is an unverified paraphrase that T10 clusters.

## 4. Prioritization

**P0 — now:** (1) signal history + account assessment layer; (2) leak-free `as_of`; (3) the
stealth-churn rule set with a coverage guard; (4) timing-aware evals and Northbeam v3; (5)
feedback and outcome primitives; (6) resequence the roadmap.

**P1 — with the first design partners:** Churn Replay v1; weekly account review email; plays;
extraction v2 (budget/scope direction, initiatives); evidence strength and caveats; expansion
hypotheses; pre-meeting brief.

**P2 — later:** Slack and transcripts; invoice import for payment drift; per-workspace calibration
report; follow-up drafts (never sent); margin/pricing; job-post insourcing; LLM narrative or skeptic
only if evals demand it.

**P3 — wait for customer evidence:** cross-workspace priors; probabilistic churn models;
autonomous low-risk actions; MCP; ICP report; package discovery; "transactional vs strategic";
multi-agent orchestration.

**Not worth doing:** a user-facing 0–100 health score; LLM self-reported confidence; a
risk-agent-vs-skeptic-agent debate as core architecture; per-person sentiment or responsiveness;
auto-sending anything to clients.

## 5. Proposed product model

- **Onboarding:** connect → map clients → mark lost clients → backfill → Churn Replay report.
- **Monday:** account review email — accounts whose state changed; ≤ 3 drivers each; what is
  coming; suggested move; strength and caveat; feedback buttons.
- **Before a meeting:** deltas, open commitments, unknowns, 3 questions.
- **On demand:** client page as drill-down (state timeline, drivers, evidence, commitments,
  stakeholders, unknowns).
- **Monthly (30 s):** outcome check. **Quarterly:** calibration report.

Pipeline: source records → extraction + evidence → account memory → account metrics → signals
with history → account assessment → play selection → card wording (LLM + verify) → founder
feedback → outcomes → calibration (owner-approved config changes).

Keep: ingest, minimization, policy, RLS, composite FKs, purge, clean, verify, evidence model, signals
engine design, memory, unknowns, latent needs, Advise Me gate idea, F1 design, drafts-only,
Northbeam + scorer + oracle. Change: signal persistence (+ history), cards from assessments with
structured fields, time-aware evals, as-of facts, extraction direction/initiatives, roadmap order,
revenue at risk as plain MRR. Defer: numeric scores in UI, MCP, ICP report, package discovery,
five-locale generated text for the pilot, the portfolio grid landing page.

Example card (synthetic Northbeam, Corvid Logistics, as of 2026-09-30): Watch → Elevated since
2 Sep. *What changed:* champion Maria Santos left on 21 Aug; the new CMO wrote "I'd like to review
every agency contract in my first 60 days."; client email volume well below its baseline; our SEO
summary promised for 9 Sep is open. *What is coming:* the review window ends about 1 Nov; the
notice deadline for the 1 Feb 2027 renewal is 3 Nov; MRR 9,000 USD. *Move:* deliver the overdue
summary this week, then a founder-led introduction call before the review ends (play: stakeholder
re-mapping). *Strength:* strong — three independent drivers; caveat: some volume drop is expected
during a handover. *Feedback:* Useful · Knew already · Wrong · Not now · I'll do it.

# Signals across mail and Slack (L2S-08 specification)

Written 2026-10-05 by the lead. Implemented by L2S-09 (Codex), tested independently by L2S-10 (Muse Spark) from this file, exercised end to end by the Northbeam Slack scenarios (L2S-11).
Today every volume and latency rule reads mail only. With Slack as a source a client who moves from mail to Slack would look like falling engagement (a false alarm, worse than none), and a founder
who is not on every thread would get false "silence" signals. The rules below keep the existing behaviour for mail-only clients (`signals.v2` results must not change for them) and add `signals.v3`.

## 1. Facts

- `RecordFact.source: Literal["email", "chat"]` (default `"email"`), set by the loader from `source_record.kind` (`email` or `chat`). Calendar events stay in `events`.
- `ClientFacts.timezone: str` (the workspace time zone, `UTC` when unknown) and `ClientFacts.owner_person_ids: frozenset[UUID]`: the firm-side people that **are the owner**: persons whose identity equals an email address of an active mail connection of the
  owner, or the Slack user id of the owner's Slack connection (`auth.test`). Empty when unknown (then rule 5 does not trigger).
- **Chat side and direction:** in a Slack Connect channel an author from another organisation (`is_external` or a `team_id` different from the connection's team) is **client** side; the owner's team is **firm** side. Client-side chat messages have
  `direction = in`, firm-side `out`, internal channels `internal`. Loader and resolver already treat `author_side`; chat just fills it differently.
- As-of rule (D5, no hindsight) is unchanged: `occurred_at <= ref`. Limitation, accepted and documented: an edited chat message is stored in its latest text and a deleted one is gone, so a replay cannot know the earlier state.

## 2. Client touches (volume rules)

`ClientFacts.client_messages()` returns **client touches**, oldest first:
- every client-authored email record, as today;
- client-authored chat records **collapsed into bursts**: consecutive client-authored chat records in the same channel (or the same thread) with gaps of at most `chat_burst_minutes` (30) count as one touch, represented by the first record of the burst.

Rules that use touches: `reduced_engagement`, `client_initiation_drop`, `participant_narrowing`, `decision_maker_silence` (authors across both sources). Baselines use the same function over the baseline window, so a client moving from mail to Slack shows continuity:
the mail touches fall, the chat touches rise, the total stays.
`tone_decline` keeps counting **email records only** in `signals.v3` (tone from short chat messages is unreliable; chat tone items are stored and shown, but do not feed this rule). Revisit with pilot data.

## 3. Reply latency per source

`champion_latency_drift` and `client_reply_latency_drift` compute reply gaps **within the same source**: a reply is a client record whose previous record in the same thread (email thread key, or the same Slack channel and thread) is firm-authored `out`.
- Email gaps: wall-clock hours, thresholds unchanged.
- Chat gaps: **working hours** between the two timestamps in `ClientFacts.timezone` (Monday to Friday, 09:00 to 18:00; time outside is not counted). Pure function `working_hours_between(a, b, tz)`, unit-tested including weekends and daylight saving.
- Each source is evaluated separately with its own thresholds and may fire its own signal: `dedupe_key = "latency:email"` / `"latency:chat"` (the existing key `latency` stays for email so old rows keep matching), `inputs.source` records which.

`signals.v3` config additions (all other rule keys unchanged):

```yaml
version: signals.v3
chat_burst_minutes: 30
partial_view:
  window_days: 120
  min_other_firm_people: 2
  max_owner_share: 0.5
rules:
  client_reply_latency_drift:
    chat:
      min_recent_replies: 5
      min_baseline_replies: 10
      min_recent_median_wh: 3
      watch_median_wh: 1.5
      min_ratio: 2
      watch_ratio: 1.5
  champion_latency_drift:
    chat:
      min_recent_replies: 4
      min_baseline_replies: 8
      min_recent_median_wh: 3
      min_ratio: 2
```

## 4. Coverage guard

`CoverageFact` is provider-agnostic: a Slack connection that carried the client's baseline records (days 31 to 120) counts exactly like a mail connection. If any baseline connection (mail or Slack) is no longer active or stopped syncing more than
`coverage.tolerance_days` (3) before `ref`, every `uses_volume` rule is suppressed and `coverage_changed` is added (existing mechanism, no new code besides including Slack connections in the loader).

## 5. Participant coverage: the owner is not on every thread

Firra only sees the owner's mailbox and Slack. When other people at the firm talk to the client in threads the owner is not on, the owner's view shows "silence" that is not real. This cannot be seen directly, so use a conservative proxy over the last
`partial_view.window_days` (120) days, evaluated at `ref`:

`partial_view = len(firm-side authors who are not the owner) >= min_other_firm_people  AND  owner_share < max_owner_share`, where `owner_share` = records authored by the owner / all firm-authored records (both sources). It is also true when the owner authored **no** record
in the window although firm-side records exist. It is false when `owner_person_ids` is empty or there are no firm-authored records.

Effects when `partial_view` is true: every `uses_volume` rule is suppressed (same switch as the coverage guard) and the assessment adds the caveat **`participant_coverage`**: "Firra sees only your mailbox and Slack; other people at your firm also talk to this client." Rules that rest on content and
dates (complaints, commitments, stakeholder changes, renewals) are not suppressed. The caveat texts and the MCP `caveats` list carry the new code. Thresholds are a first guess to be tuned on pilot data (`status-local.md` records every false alarm that traces to it).

## 6. Evidence and deep links

Evidence items gain `source: "email" | "slack"`; for Slack `deep_link` is the message permalink and `locator` holds the message `ts` plus character offsets of the quote in the cleaned text. MCP evidence output keeps `untrusted_quote` and adds `source`.

## 7. Oracle cases (added to `services/worker/tests/unit/signals/oracle.yaml`; L2S-10 writes them independently from this list)

| # | Situation | Expected |
|---|---|---|
| O1 | Client wrote 8 emails per 30 days in days 31–120, then 0 emails but 8 chat touches in the last 30 days | no `reduced_engagement` |
| O2 | Same baseline, then 0 emails and 0 chat in the last 30 days | `reduced_engagement` high |
| O3 | 30 client chat messages in 3 bursts (gaps under 30 min inside a burst) | counts as 3 touches |
| O4 | Chat: baseline replies median 0.5 working hours (≥ 10 replies), recent median 4 (≥ 5 replies) | `client_reply_latency_drift`, `inputs.source = chat` |
| O5 | Chat: a reply at 09:15 to a firm message at 17:45 the previous Friday/evening | gap counts 0.5 working hours (or the weekend rule), no drift |
| O6 | Email latency scenario of `signals.v2` | identical result to v2 |
| O7 | `complaint` extraction on a chat record from a client-side author | `delivery_complaint` citing evidence with `source: slack` |
| O8 | A firm commitment extracted from Slack, due date passed 20 days ago, open | `late_commitment` |
| O9 | ≥ 2 other firm people authored in the window and owner share 0.3 | volume rules suppressed, caveat `participant_coverage` |
| O10 | Same people, owner share 0.8 | rules active, no caveat |
| O11 | Owner authored nothing, other firm people wrote | `participant_coverage` |
| O12 | A Slack connection that stopped syncing 20 days ago carried the baseline | volume rules suppressed, `coverage_changed` |
| O13 | Chat records dated after `ref` | facts at `ref` identical to facts without them (leakage property test) |
| O14 | Negative tone item on a chat record | does not change the `tone_decline` result |
| O15 | `owner_person_ids` empty | no `participant_coverage`, behaviour as v2 |

## 8. Northbeam Slack scenarios (L2S-11, lead writes the truth, Codex the generator)

(a) a benign control client moves from mail to a shared Slack channel in month 6 and must stay below `elevated` for the whole year; (b) a client that churns whose complaints and delays appear **only** in Slack and must be detected at least 30 days before notice;
(c) a firm commitment made only in Slack that goes late; (d) one client talked to mostly by an account manager (owner share low) where volume signals must be suppressed and the caveat shown. The existing three churn losses, two benign controls and all T49e routine eval gates must stay green.

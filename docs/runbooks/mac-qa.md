# Firra Mac screens QA (L5-08 style)

Manual walkthrough of onboarding and every main screen. Run by hand on a test
workspace with synthetic mail (the L2-04 test account), after clean-install
sections 0–3. Never use real client data. Status words below describe `main`
at the time of writing: **exists** (runs, may still need its L5 port),
**not yet** (write the step anyway; mark N/A until it lands).

Record before starting: app version, date, runner, engine status from
`/local/status` if reachable.

## 0. Screen inventory on main

| Screen | State on main |
|---|---|
| Onboarding (welcome → privacy → Google → backfill → clients → AI) | **not yet** (L5-03 builds it; sign-in screens from the cloud era still exist) |
| Today / What changed | exists (`AttentionView`, `ChangesView`) |
| Clients | exists (`ClientsView`) |
| Client detail | exists (`ClientDetailView`, champion/unknowns/quotes) |
| Replay | exists (`ReplayView`) |
| Routines (switches) | exists (`RoutinesView`, `RoutinesModel`) |
| Mail | exists (`MailVaultView`, still on vault; engine port L5-07 pending) |
| Decisions | exists (`DecisionsView`, importance) |
| Settings (clients, policy, AI, Slack, account) | **not yet** as one place (pieces exist; L5-03/L2S-13 assemble them) |
| Connect AI tools | exists (`AIToolsView`) |
| Menu bar + notifications | exists (`MenuBarContent`, `NotificationPlanner`; Notch* is a separate experiment) |
| Team / Contributors | exists but **slated for removal** (L5-04; must be gone, not tested) |

## 1. Onboarding (not yet — run when L5-03 lands)

1. Fresh install → welcome → privacy screen.
   **Expected:** the privacy promises (mail stays on the Mac, owner's AI only)
   before any account is connected.
2. Connect Google with the test account, choose 3-month backfill.
   **Expected:** progress with counts; finishes without errors.
3. Client suggestions → confirm clients, set one `metadata_only`.
   **Expected:** the suggestion list, policy switch persists.
4. AI processing step with Claude Code installed / without it.
   **Expected:** background vs in-conversation routes match the machine.

## 2. Today

1. Open Today with yesterday's churn-replay-style changes present.
   **Expected:** "N clients need attention" in the menu bar matches the list;
   items ranked, each with drivers and at least one evidence link.
2. Pull new mail (or wait for a sweep).
   **Expected:** What changed updates within minutes; no duplicates.

## 3. Clients

1. Open Clients, search by name and by domain.
   **Expected:** every active client listed once; `excluded` absent;
   `metadata_only` shows counts, never quotes.

## 4. Client detail

1. Open an active client with history.
   **Expected:** state, what changed, drivers with evidence, stakeholders with
   champion, commitments per side, goals, unknowns, needs, play, history —
   every claim has an evidence link with a quote.
2. Open the `metadata_only` client.
   **Expected:** stakeholders, timeline and unknowns only; no body-derived
   facts, no quotes.

## 5. Replay

1. Pick a churned test client, run replay.
   **Expected:** week-by-week states, first watch/elevated week, signal
   families behind each move, the play it would have recommended.

## 6. Routines

1. Settings → Routines: flip morning scan off and on.
   **Expected:** switch persists across restart; off means no cards.
2. Trigger a commitment going late (test clock or fixture).
   **Expected:** at most 3 `important` notifications per week; FYI stays in-app.

## 7. Mail

1. Open Mail, search a known sentence, open the message.
   **Expected (after L5-07):** reads from the engine; before that, vault
   content must match the engine's evidence context. No raw bodies in logs.

## 8. Settings

1. Change a client policy full → excluded → full.
   **Expected:** excluded hides everything immediately; restoring brings back
   only re-synced data, no duplicates.
2. Disconnect Google, reconnect.
   **Expected:** status reflects it; sync resumes without duplicates.
3. Connect AI tools screen: one button per tool.
   **Expected (manual):** Claude Code command, Cursor/VS Code deeplinks,
   Desktop `.mcpb`, Codex command, claude.ai/ChatGPT link each do something
   sane (record per-button result).

## 9. Menu bar states

1. Force each state: syncing, AI queue ("N emails waiting"), executor error,
   relay offline, Mac offline.
   **Expected:** icon + text match `/local/status`; offline states say plainly
   the Mac or relay is unreachable, never a spinner forever.

## Result sheet

```
version / date / runner:
screens run (list N/A for not-yet):
failures (screen, step, what happened):
missing screens confirmed still missing:
team UI still present (must be gone per L5-04: yes/no):
```

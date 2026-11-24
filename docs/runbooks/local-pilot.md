# Local pilot runbook (L10-03)

For the owner running a pilot with 2–3 founders. Requirements per partner: `docs/runbooks/pilot-requirements.md`. Legal drafts that must be reviewed and signed first: the legal drafts (not included). Replaces the cloud-era `design-partner-pilot.md`.

## 0. Before the first partner (owner, once)
1. A lawyer has reviewed the legal drafts (not included) (EULA, privacy policy, AI provider notice) and the partner signed the EULA. Open legal questions are listed in the legal drafts (not included).
2. Google: a Google Cloud project with the **Desktop app** OAuth client; `google-oauth-client.json` is placed in `~/Library/Application Support/Firra/` on the partner's Mac (the app reads it; the file holds the client id/secret of the app, not user data). The partner's Gmail address is added as a **test user** (refresh tokens expire after 7 days in Testing mode: the app asks to reconnect).
3. A DMG built from `main`: `apps/mac/scripts/build-app.sh <version>` (Apple Silicon Mac). Check the SHA-256 and hand it over by a link you control. **Do not hand out DMGs built before 2026-10-06** (Keychain bug: the app could not start).
4. Decide the update path: until the release bucket exists (needs the cloud work on hold), partners get new DMGs from you by hand.

## 1. Partner day 0 (30 minutes, together on a call)
1. Mac: Apple Silicon, macOS 14+. **Turn FileVault on** (System Settings, Privacy & Security) — Firra warns if it is off, and it is the second layer of protection.
2. Install: open the DMG, drag Firra to Applications, **right-click, Open** (the app is not notarized).
3. First start: the app copies its engine (about 135 MB) and starts it; this takes under a minute. If the window says **"Firra stopped"** read the line "Last problem:" and `~/Library/Application Support/Firra/log/engine-stderr.log`.
4. Onboarding: privacy page (read the AI-plan warning aloud), connect Google (browser, partner approves), choose backfill depth (3 months default), pick clients and policies (`full` / `metadata_only` / `excluded`), choose AI processing.
5. AI processing: **recommend** Claude Code signed in (`claude` in Terminal). Say plainly that background processing uses their own subscription and may be restricted by their provider's terms on consumer plans; "only in conversation" is equally supported. Do not offer Codex as a background executor (not built).
6. Optional Slack: "Connect Slack" gives a manifest; the partner creates an internal app in their workspace and pastes the user token. Map Slack Connect channels to clients.
7. Connect their AI tools (Connect AI tools screen): Claude Code (`claude mcp add …`), Cursor, VS Code, Codex. Over a link (claude.ai/ChatGPT) is **not available until the relay is hosted** (cloud work on hold).
8. Settings, **Start Firra when I log in**: switch on. If macOS refuses (ad-hoc signature), add Firra under System Settings, General, Login Items by hand.
9. Source access ("let my AI read excerpts") stays **off** unless the partner decides to switch it on after reading the explanation.

## 2. First week
- Metadata signals appear within minutes of the first sync; content-based signals appear as the partner's AI works through the queue (a status line shows "N emails waiting"). If the AI hits its usage limit the queue pauses and resumes.
- Ask the partner to rate every item that Firra surfaces (useful / already knew / wrong / not now) — this is the pilot's main measurement.
- Notifications are for *important* only, at most 3 per week; the partner keeps them on for 2 weeks.

## 3. Weekly (owner)
1. The partner exports metrics: a JSON with usefulness, precision, lead time and *important* per week — no names, ids or text. Settings, Data and privacy, **Export metrics** saves it; they send the file by email.
2. Check every item they rated "wrong" together: the reason goes to the design notes (false alarms trace to rule thresholds in `config/signals.yaml`).
3. If something broke: the partner exports diagnostics (versions, counts, error classes; no content) and sends it.

## 4. Troubleshooting
| Symptom | First checks |
|---|---|
| "Firra stopped", "Last problem: …" | read the line; `log/engine-stderr.log` (engine output, no client content); restart from the menu bar |
| Google asks to reconnect weekly | expected in Testing mode (7-day refresh expiry); reconnect from the Google account screen |
| No mail after connect | Connections screen shows sync state and `last error class`; confirm the client domains are mapped (unmapped mail is discarded by design) |
| AI queue not moving | Settings, AI: executor status (not installed / not signed in / limit reached); the in-conversation route always works |
| Partner wants out | Settings, **Delete everything** (data directory, Keychain items, token revoke); optionally an encrypted backup first |

## 5. What not to promise
No numeric health score, no auto-sent messages, no coverage of colleagues' private mail (Firra sees only the partner's own mailbox: assessments carry a participant-coverage caveat), no claude.ai/ChatGPT link yet, no automatic updates.

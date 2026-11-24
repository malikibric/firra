# Firra clean-install protocol (L6-04)

Manual end-to-end check on a clean macOS account, from DMG to uninstall. Run
it by hand once the L6-01 DMG exists; most steps cannot run before then. It
covers plan §1 items 1 and 12 and gates G-E ("DMG works on a clean Mac").

Use a test Google account (L2-04, e.g. `firra.test.northbeam@gmail.com`) and
synthetic mail only. Never use real client data for this run.

Record before starting: DMG version + SHA-256, date, runner, machine model,
macOS version, and whether the account is a fresh macOS user.

Prerequisites: Apple Silicon Mac, macOS 14+, the `Firra-<version>.dmg` from the
release bucket, its published SHA-256, a test Google account, and (for the AI
steps) Claude Code or Codex signed in — or test the "only in conversation"
path instead.

## 0. New macOS user

1. System Settings → Users & Groups → Add User (Administrator not required).
   Log in as the new user.
   **Expected:** a clean account: no `~/Library/Application Support/Firra`,
   no Firra items in Keychain Access, no Firra login item.

## 1. Verify and install the DMG

1. Compare hashes:
   `shasum -a 256 Firra-<version>.dmg`
   **Expected:** identical to the published SHA-256 in `latest.json`.
2. Open the DMG, drag Firra to Applications, eject the DMG.
   **Expected:** `/Applications/Firra.app` exists. Nothing starts on its own.

## 2. Gatekeeper first launch

1. Double-click Firra in Applications.
   **Expected:** macOS refuses ("cannot be verified" / unidentified developer).
   The app does not open. (Ad-hoc signature, no notarization yet.)
2. Right-click Firra → Open → Open in the dialog.
   **Expected:** the app opens. First start is slow: it copies the engine into
   Application Support and clears quarantine on the copy (R1 fallback). No
   admin password is asked.
3. If it still refuses: System Settings → Privacy & Security → Open Anyway.
   **Expected:** same as step 2. Record which path worked.

## 3. Onboarding

1. Welcome + privacy screen.
   **Expected:** states plainly what goes where (mail stays on the Mac; only
   the owner's chosen AI sees text; consumer AI plans may train unless
   switched off).
2. Connect Google → system browser opens → sign in with the TEST account →
   grant `gmail.readonly`, `calendar.readonly`, `openid email` → browser asks
   to return to Firra.
   **Expected:** back in the app, connection shows the test address and
   "syncing". No other account is offered.
3. Backfill depth: choose 3 months.
   **Expected:** progress counts up ("N emails waiting" if no AI tool is
   connected yet). 12-month choice stays available in Settings.
4. Clients: type 2–3 clients (name + email domain, e.g. `acme.com`) and set one to `metadata_only`. The optional "Suggest domains from my mail" button reads headers only when pressed; the first mail read happens after the clients are saved and covers only those domains.
   **Expected:** client list appears; the `metadata_only` client shows counts
   but no quotes anywhere.
5. AI processing: with Claude Code installed and signed in, the screen offers
   background extraction; without it, "only in conversation".
   **Expected:** the detected state matches the machine. Both paths reach
   "first results".
6. First results (Today screen).
   **Expected:** the test clients with states, drivers and evidence links;
   mail bodies open from the engine (no vault errors).

## 4. System integration

1. System Settings → General → Login Items.
   **Expected:** Firra is listed (UNVERIFIED with an ad-hoc signature — if it
   is missing, add it by hand per the fallback and record that).
2. System Settings → Privacy & Security → FileVault.
   **Expected:** on. If off, the app must have warned during onboarding.
3. In Terminal:
   `ls -ld ~/Library/Application\ Support/Firra ~/Library/Application\ Support/Firra/pg`
   **Expected:** both `drwx------` (0700), owned by the test user.
4. `lsof -nP -a -p <postmaster-pid> -iTCP -sTCP:LISTEN` (pid from Activity
   Monitor).
   **Expected:** empty. No TCP listener except the engine's own 127.0.0.1 API.
5. Keychain Access → search "Firra".
   **Expected:** Google refresh token, Slack token if connected, database
   encryption key, local API token entries exist. No plaintext secrets in
   `~/Library/Application Support/Firra/` (spot-check with grep for the test
   account address — it must not appear in logs).

## 5. Restart and wake

1. Apple menu → Restart. Log back in as the test user, do not open Firra.
   **Expected:** the menu-bar icon appears on its own within a minute.
2. Open Today.
   **Expected:** same clients and states as before the restart; sync resumes
   ("catching up" if mail arrived while off, then current).

## 6. Upgrade (needs two DMG builds, N and N+1)

1. With version N installed and data synced, note client counts. Install
   version N+1 over it (repeat section 1 with the new DMG).
   **Expected:** data kept — same clients, same counts, same feedback and
   mappings. A pre-migration backup of the non-recoverable state exists
   (location from the app or release notes).
2. Open the app.
   **Expected:** starts normally, no re-onboarding, no duplicate routines.

## 7. Uninstall (verifies L8-03 deletion)

1. In the app: delete one client first (Settings → client → Delete).
   **Expected:** the client, its records and derived data are gone; others stay.
2. "Delete everything" in Settings (or the documented equivalent), quit the
   app, then delete `/Applications/Firra.app` and
   `~/Library/Application Support/Firra`.
   **Expected:** the data directory is gone entirely.
3. Keychain Access → delete remaining `Firra` items. Google account →
   myaccount.google.com/permissions → remove Firra access. Slack →
   disconnect (revokes the user token).
   **Expected:** no Firra secrets or grants remain anywhere.
4. Reinstall the same DMG and open the app.
   **Expected:** clean onboarding from scratch, as in section 3. Proof that
   deletion was complete.

## Result sheet

Copy, fill in, attach to the release notes:

```
version / SHA-256:
date / runner / machine / macOS:
fresh user (yes/no):
2 Gatekeeper path (double-click refused / right-click / Open Anyway):
3 onboarding (pass/fail + notes, AI path used):
4 login item present (yes/no/manual) / FileVault on (yes/no) / modes 0700 (yes/no) / lsof empty (yes/no):
5 restart keeps state (yes/no):
6 upgrade keeps data (N -> N+1, counts before/after):
7 uninstall complete (reinstall shows clean onboarding: yes/no):
blockers:
```

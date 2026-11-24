# Firra for Mac

Native menu-bar app (SwiftUI, macOS 14+, Apple Silicon). It starts and supervises the **local engine**
(bundled Python + PostgreSQL) and talks to it on 127.0.0.1; all client data stays on this Mac.
Plan: `docs/plans/local-v1.md`.

- `Sources/FirraKit`: engine supervision (control socket, secrets handshake, installer), Google Desktop
  OAuth, the local API client and view models. Testable without a UI.
- `Sources/FirraApp`: the SwiftUI shell (menu bar, onboarding, screens).
- `scripts/test.sh`: Swift tests. `scripts/build-app.sh [version]`: builds `build/Firra.app` and
  `build/Firra-<version>.dmg` (+ SHA-256) on this Mac; it builds the engine bundle (PostgreSQL 17 +
  pgvector from source, Python 3.12 with wheels) into `build-engine/` the first time and reuses it.
- `scripts/build-postgres.sh`, `scripts/build-python.sh`, `scripts/smoke-engine.sh`: engine bundle
  pieces (`docs/research/engine-bundle.md`).

## Install (no Apple Developer account)

Open the DMG, drag Firra to Applications, then **right-click -> Open** the first time (the app is
signed with a local certificate, not notarized). The app copies its engine into
`~/Library/Application Support/Firra/engine/<build>` and clears quarantine on the copy.

Signing: with a code signing identity named in `FIRRA_SIGN_IDENTITY` (default `Firra Local Signing`,
a self-signed certificate in the login keychain) the app keeps the same signature requirement across
builds, so macOS asks for Keychain access once; without it the app is ad-hoc signed.

The Google OAuth client (type "Desktop app") is read from
`~/Library/Application Support/Firra/google-oauth-client.json` until onboarding bundles it.

## AI source access

Onboarding’s AI processing step and **Connect AI tools** share the switch **Let my AI read excerpts
of client mail, Slack and meetings**. It is off by default. Enabling it requires confirmation that
client excerpts go to the owner’s chosen AI provider; access is limited to one added client at a
time. Disable it at any time without a confirmation. The local engine stores the value, so **Run
setup again** preserves it. Loading or saving failures show a retry message; a failed save restores
the previous switch value.

## AI extraction tools

Only **Connect AI tools** offers **Let my AI process new mail in the conversation**. It is off by
default; enabling it asks for confirmation. Firra already processes new mail in the background with
the owner's Claude Code. This switch lets the owner's AI also fetch mail batches and submit
extractions in conversation. Each batch uses about 15,000 tokens of the owner's AI plan; leave it
off unless background AI is unavailable. Changes take effect after restarting Firra and the AI
tool. Turning it off needs no confirmation. Loading and saving show progress, errors offer a
retry, and failed saves restore the previous value. Source access remains a separate switch.

## Text retention

In **Settings > Data and privacy**, choose how long to keep mail, Slack and meeting text: 3, 6,
12 (default) or 24 months, or **Until I delete it**. Shortening requires confirmation and takes effect
immediately; deleted older text cannot be recovered by undoing the setting. Dates, participants,
short verified quotes and learned facts remain. A failed save restores the previous picker value.

## Connector links

In **Connect AI tools > claude.ai and ChatGPT (over a link)**, set the https relay address Firra gave
you and create a named connector link. Client intelligence is always included; source excerpts are
unchecked by default and also require the separate source-access switch. The URL is shown once:
click **Copy** and paste it as a custom connector with no authentication. Treat it like a password;
anyone with it can ask about your clients. Revoke a link here to stop access immediately. The AI
receives “Firra on your Mac is offline” while this Mac sleeps or Firra is closed.

## Updates (L6-02)

`FIRRA_UPDATE_FEED=https://<bucket>/latest.json apps/mac/scripts/build-app.sh <version>` bakes the feed address into the app (`FirraUpdateFeed`,
https only; without it the check is off). Once a day the app reads the feed (`{version, url, sha256, notes}`, at most 16 KiB, validated), and when
the version is newer Settings shows it with the download link and the SHA-256 to compare (`shasum -a 256`). Nothing is downloaded or installed
by the app. `scripts/publish-release.sh <dmg> <version> "<notes>"` prints the feed entry; with `--yes` and `FIRRA_RELEASE_BUCKET` it uploads the
DMG and `latest.json` (owner's go).

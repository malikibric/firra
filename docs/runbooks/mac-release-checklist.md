# Firra for Mac: manual release checklist

Run this by hand before sending a DMG to a design partner. It replaces the XCUITest suite we cannot
run (there is no Xcode on the build Mac; see the open gaps in `docs/plans/d-mac-app.md`). Every step
says where to look and what you should see.

Use a test workspace with made-up clients and mail. Never use a real partner's data for this run.

Record before starting: build version, date, and who ran the checklist.

## 1. Install from the DMG

1. Open `Firra-<version>.dmg` and drag Firra to Applications.
   **Expected:** Applications shows Firra. The app does not start on its own.

2. Double-click Firra in Applications.
   **Expected:** macOS refuses to open it ("the developer cannot be verified" / unidentified
   developer). The app does not open.

3. Right-click Firra → Open → Open. If macOS still refuses, System Settings → Privacy & Security →
   Open Anyway.
   **Expected:** the Firra window opens on the sign-in screen. The menu bar shows the Firra hexagon
   icon.

## 2. Sign in

1. On the sign-in screen click "Continue with Google" and choose an account in the system sheet.
   **Expected:** the sheet closes and the app shows "Needs attention" (or a workspace picker if the
   account is in more than one workspace).

2. Open "Account" → "Sign out" (quitting and reopening does not sign you out). Then click "Continue
   with Microsoft" and choose a Microsoft account.
   **Expected:** the same result with a Microsoft account.

3. Sign out again. On the sign-in screen type a work email and click "Email me a sign-in link".
   **Expected:** the screen says "Check your inbox: the link opens Firra and signs you in."

4. Open that email and click the link.
   **Expected:** the app comes to the front through the `firra://auth-callback` link and signs in. No
   browser tab stays open.

5. Sign out. Then start "Continue with Google" and close the system sheet without choosing an
   account.
   **Expected:** back on the sign-in screen with no error message and no spinner. Nothing was signed
   in.

6. Sign out, then sign in with a different account than the last one.
   **Expected:** the app signs in as that account. No client or client name from the previous
   account is shown. To get back to the first account, sign out and sign in again.

## 3. Session

1. While signed in, quit Firra from the menu bar and reopen it.
   **Expected:** still signed in; the needs-attention list loads; no sign-in screen.

2. Open "Account" → "Sign out".
   **Expected:** the sign-in screen returns. Quit and reopen: still signed out.

3. After signing out, look in Finder at `~/Library/Application Support/Firra/`.
   **Expected:** the `cache` folder is gone. The mail `Vault` folder may stay (local mail is deleted
   in the app, not by signing out). Signing in as another user shows none of the first user's list.

4. Simulate a revoked session: in the Supabase dashboard for the staging project, sign the user out
   of all sessions. Leave Firra open. The access token stays valid until it expires (one hour with
   the default settings), so this step takes up to an hour. After that, press the Refresh button on
   "Needs attention" (Firra also checks every 15 minutes).
   **Expected:** the app returns to the sign-in screen. No old clients flash on screen first. If you
   press Refresh right after the dashboard sign-out and the list still loads, that is normal: the old
   access token is still valid. Do not count it as a failure; wait and press Refresh again.

## 4. Offline

1. Sign in, open "Needs attention", and note the clients shown.

2. Turn Wi-Fi off. Quit Firra and reopen it (or press Refresh).
   **Expected:** the same clients are still listed. An orange banner reads "Offline: this is the last
   list Firra saved; it could not be refreshed." The menu bar summary ends with "(not refreshed)".

3. While still offline, open every screen and switch workspaces if the account has more than one.
   **Expected:** no client from another workspace or another user appears. A workspace that was never
   loaded stays empty instead of showing the previous workspace's list.

4. Turn Wi-Fi on and press Refresh.
   **Expected:** the banner disappears and the list refreshes.

## 5. Roles

1. Sign in as an owner or admin and read the left sidebar.
   **Expected:** the sidebar lists, in this order, Needs attention, What changed, Decisions, Clients,
   Connections, Import mail, Replay history, Audit log, Services, Team, Contributors, Mail on this Mac,
   Connect AI tools and Account. The signed-in email address is shown below the list.

2. As the owner/admin, open "Contributors", invite a made-up contributor, and copy the invite code.

3. Sign in on the other Mac as that contributor and enter the invite code.
   **Expected:** after joining, the app shows the contributor home: the workspace name, a five-line
   notice of what is collected ("Firra reads only mail that involves your firm's clients." …), only
   the contributor's own mailbox with "Connect Google" / "Connect Microsoft", and "Mail on this Mac".
   No client, no Needs attention, no Decisions.

4. As the contributor, connect a mailbox, then disconnect it and confirm.
   **Expected:** the connection disappears from the list. Mail already delivered stays under "Mail on
   this Mac".

## 6. Privacy: metadata only and excluded

Run this on a workspace that has a full-policy client with mail and evidence.

### Metadata only

1. As an owner/admin open "Clients", open a mapped client, set "Data policy" to "Metadata only", and
   Save.
   **Expected:** the sheet warns that switching to Metadata only removes stored text for this client
   now.

2. Open that client with "Details".
   **Expected:** the header says "Metadata only: no quotes". Under "Why", drivers are marked "from
   message counts and dates" and show no quotes. There is no "Open commitments", "Goals they stated"
   or "Recurring asks" section.

3. Open "What changed" and any recent event for that client, then open "Decisions".
   **Expected:** no quote is shown for that client anywhere. Events and cards show counts, dates and
   wording only, with no "Read the whole mail" and no "Open original".

4. Open "Replay history" and open that client's replay.
   **Expected:** only states, counts and dates. If the stored replay had quotes, the app says this
   replay has no story for that client instead of showing them.

5. Hover or click the notch pill and open any meeting brief for that client.
   **Expected:** only the client name, states, counts and dates, and at most renewal or unknown
   questions. No quote.

### Excluded

6. Set a client's policy to "Excluded", turn on "I understand this deletes what Firra holds about
   this client", and Save.
   **Expected:** the client disappears from Needs attention, What changed, Decisions, Replay history
   and the notch list right away.

7. Open "Mail on this Mac".
   **Expected:** the excluded client's messages are gone and the Messages count dropped.

8. Open "Clients".
   **Expected:** the excluded client is still in the setup list, labelled "Excluded", and has no
   "Details" button. It stays so an owner can change its policy back.

## 7. Mail on this Mac

1. With at least one full-policy mailbox connected and some mail processed, open "Mail on this Mac".
   **Expected:** the screen says "Your mail stays on this Mac", shows a Messages count, a "Last
   fetched" time, and a list of recent subjects.

2. Quit Firra and reopen it, then open "Mail on this Mac" again.
   **Expected:** the same messages are still listed (the vault survives a restart).

3. Click "Fetch my mail again".
   **Expected:** a message appears: "Asked N mailbox(es) to send your mail again. It arrives here
   within minutes." or "No connected mailbox of yours was found." if none is connected. New mail
   appears within a few minutes and "Last fetched" updates.

4. Open "Needs attention", open a client, find a quote and click "Read the whole mail".
   **Expected:** the whole message opens from the local vault. If it was not delivered to this Mac,
   an alert says so and offers "Open original".

## 8. Connect AI tools

1. Open "Connect AI tools", type a name and click "Create".
   **Expected:** a yellow banner "Copy this token now. It is shown only once." shows the token, a
   "Copy token" button, and a "Claude Code" command line.

2. Click "Copy token" and paste somewhere to confirm it copied. Wait a full minute without copying
   anything else, then paste again.
   **Expected:** the clipboard is empty after a minute (the token was removed). Copying something
   else in the meantime keeps that instead.

3. Click "I have copied it", leave the section, then come back.
   **Expected:** the token is never shown again. It is listed under "Active tokens" with its prefix,
   expiry and "never used".

4. Compare the shown Claude Code command with the "Server address" on the same screen.
   **Expected:** the command starts `claude mcp add --transport http firra`, uses the same server
   address plus `/mcp`, and ends with `--header "Authorization: Bearer <token>"`.

5. Click "Revoke" on the token.
   **Expected:** the token disappears from "Active tokens".

## 9. Notch pill

1. In the menu bar menu, turn "Show notch pill" on.
   **Expected:** a small pill appears at the top centre of the screen (over the notch, or the top of
   the main screen). It shows a colour dot; hovering shows a headline; clicking shows up to three
   clients.

2. Turn "Show notch pill" off, then on again.
   **Expected:** the pill disappears, then comes back.

3. With the pill on, quit Firra and reopen it.
   **Expected:** the pill is shown again (the choice was remembered).

4. Click the pill to open the list, then press Esc, or click another window.
   **Expected:** the pill collapses back to the dot.

5. Arrange a calendar meeting mapped to a client that starts within about ten minutes, then watch
   the pill.
   **Expected:** the pill expands to a brief by itself: the client name, "starts in N min", and up to
   three questions (or "No questions yet"). Dismiss and "Open Firra" both work.

6. For a quick look without signing in (debug build only), run `swift run Firra --demo-notch`.
   **Expected:** the pill appears with made-up clients and a made-up meeting, and no sign-in.

## Where this comes from

Every non-obvious Expected line above is backed by the code or a test:

**Install**

- DMG, right-click Open, Gatekeeper "Open Anyway": `apps/mac/scripts/package.sh` (builds `Firra.app`
  and the DMG, writes the `firra` URL scheme into `Info.plist`) and `apps/mac/README.md`, "Install
  (for a design partner)".
- Menu bar icon: `apps/mac/Sources/FirraApp/FirraApplication.swift` (`MenuBarExtra`).

**Sign in**

- Google/Microsoft sheet and PKCE redirect: `FirraKit/SupabaseAuth.swift`
  `authorizeURL(provider:pkce:)` and `FirraKit/AuthController.swift` `signIn(with:)`; test
  `AuthTests.SupabaseAuthTests.authorizeURLCarriesProviderRedirectAndChallenge`.
- Magic-link message and emailed link: `AuthController.sendMagicLink(email:)`; test
  `AuthTests.SupabaseAuthTests.magicLinkRequestsAPKCELinkBackToTheApp` and
  `AuthTests.AuthControllerTests.magicLinkThenCallbackSignsInAndStoresTheSession`.
- `firra://auth-callback`: `AuthController.handleCallback(_:)`, `SupabaseAuth.code(from:)` and the
  `.onOpenURL` in `FirraApplication.swift`; test `SupabaseAuthTests.codeIsReadOnlyFromOurCallback`
  and `AuthTests.SessionRaceTests.anEmailedLinkStillSignsInAfterTheAppRestarted`.
- Cancel the sheet: `AuthController.signIn` catches `SignInCancelled` and leaves the message empty.
- No previous account's data after a different sign-in: `FirraApp/AppModel.swift`
  `signedIn(user:)` retires the cache and rebuilds models per user; `FirraKit/LocalCache.swift`
  `use(user:)`; tests `CacheTests.FileResponseCacheTests.storesPerUserAndNothingWithoutOne` and
  `CacheTests.CacheRetireTests.aRetiredCacheNeverWritesAgain`.

**Session**

- Stays signed in after quit/reopen: `AuthController.start()` reads `Session.swift`
  `KeychainSessionStore`; test `AuthTests.AuthControllerTests.startRestoresAStoredSession`.
- Sign out clears the Keychain session: `AuthController.signOut()`; test
  `AuthControllerTests.signOutClearsTheKeychainItem`.
- Sign out clears the local cache: `AppModel.endSession` calls `FileResponseCache.retire()`; tests
  `CacheTests.CacheRetireTests.aRetiredCacheNeverWritesAgain` and
  `CacheTests.OfflineTests.signingOutCallsTheCacheHook`. The vault stays on disk by design (comment
  in `AppModel.endSession`).
- Revoked/expired session returns to sign-in: `AuthController.accessToken(...)` catches
  `APIError.unauthenticated` and calls `signOut()`; `AppModel.Services.make` sets `onUnauthorized`
  to `auth.signOut(ifUser:)`; tests `AuthControllerTests.failedRefreshSignsOut`,
  `OfflineTests.aRejectedAccessTokenReturnsToSignIn`, `OfflineTests.aTransientRefreshFailureKeepsTheSession`
  and `SessionRaceTests.aLateUnauthorizedOfAnOldSessionDoesNotEndTheNewOne`.
- Why step 3.4 waits: the app only asks Supabase for a new token when the access token has less than
  a minute left (`Session.needsRefresh`; `jwt_expiry = 3600` in `supabase/config.toml`), and a revoked
  refresh token fails then. The API checks the access token with `getClaims`
  (`apps/api/src/auth/tokens.ts`), which we do not rely on to reject a revoked session early. The
  15-minute poll is `AppModel` (`Task.sleep(for: .seconds(15 * 60))`).

**Offline**

- Saved list + offline banner: `FirraKit/ViewModels.swift` `AttentionModel.load(workspaceId:)` and
  `summary`; `FirraApp/AttentionView.swift` offline label; tests
  `CacheTests.OfflineTests.offlineKeepsTheLocalCopyAndSaysSo` and `withoutACopyAnErrorStaysAnError`.
- No other workspace's or user's data: `AttentionModel.load` clears items when the workspace changes;
  the cache is per user (`CacheTests.FileResponseCacheTests.storesPerUserAndNothingWithoutOne`) and
  `AppModel.Services.make` builds the models per user.

**Roles**

- Owner/admin sidebar: `FirraApp/MainView.swift` `Section` (the list is `Section.allCases`, so the
  order is the order of the cases, `Account` last) and `detail(for:)`; the email under the list is the
  `safeAreaInset` in `MainView.body`.
- Contributor home, notice, own sources and own mail: `MainView.swift` `NoWorkspaceView`;
  `FirraApp/ContributorHomeView.swift`; `ViewModels.swift` `WorkspaceModel.contributions` and
  `ContributorHomeModel`; `docs/plans/d-mac-app.md` D-D3 and `CLAUDE.md` §3.

**Privacy (metadata only)**

- Client page: `apps/api/src/lib/clients.ts` `buildClientDetail` (drops non-metadata drivers and
  empties commitments/goals/needs) and `apps/api/src/lib/read-store.ts` `clientDetail` (skips those
  reads); `FirraApp/ClientDetailView.swift` `header` ("Metadata only: no quotes") and `driverRow`.
- Timeline events: `read-store.ts` `timeline` (only `basis === "metadata"` signals, with empty
  `evidence_ids`); `ClientDetailView.swift` `TimelineEventRow` shows the disclosure only when
  `evidenceIds` is non-empty.
- Decisions: `read-store.ts` `decisions` (rejects a metadata card that carries evidence);
  `FirraApp/DecisionsView.swift` and `ViewModels.swift` `DecisionsModel.isVerified` (a card appears
  only once its quotes load).
- Replay: `read-store.ts` `replayClient` (returns nothing for a metadata client whose narrative has
  evidence; rechecks the policy after the read).
- Notch brief: `apps/api/src/lib/meetings.ts` `buildMeetings` (a metadata client gets counts, dates
  and only renewal/unknown questions); `FirraKit/APIMeetingBriefs.swift` and
  `FirraApp/Notch/NotchMeetingView.swift`.

**Privacy (excluded)**

- All read paths keep only `full` and `metadata_only`: `apps/api/src/lib/read-store.ts`
  (`decisions`, `clients`, `clientDetail`, `visible`, `changes`, `clientEvidence`),
  `apps/api/src/lib/attention.ts` `buildAttention`, `apps/api/src/lib/changes.ts`,
  `apps/api/src/lib/meetings.ts` `buildMeetings` and `apps/api/src/lib/write-store.ts`.
- The local copy and open screens drop it at once: `AppModel.signedIn(user:)` wires
  `ClientsModel.onClientRestricted` to `VaultModel.removeClient`, `cache.clearAll`,
  `AttentionModel.forget`, `ClientDetailModel.reset` and `DecisionsModel.reset`; `ViewModels.swift`
  `ClientsModel.setPolicy` calls that hook; `VaultModel.reconcile` deletes mail the server no longer
  keeps; tests `VaultTests.VaultStoreTests.deletesEveryMessageOfOneClientOnly` and
  `VaultTests.VaultKeepTests.mailOfClientsTheServerDoesNotKeepIsDeleted`.
- Still in the setup list: `apps/api/src/lib/setup-store.ts` case `"clients"` (no policy filter) and
  `FirraApp/ClientsView.swift` (no "Details" button for `.excluded`).

**Mail on this Mac**

- Screen and actions: `FirraApp/MailVaultView.swift`; `ViewModels.swift` `VaultModel` (`reload`,
  `refresh`, `fetchAgain`, `localMail`).
- Local encrypted vault and restart: `FirraKit/VaultStore.swift` (AES-GCM `seal`/`open`) and
  `FirraKit/VaultKey.swift`; tests `VaultTests.VaultStoreTests.storesAndReadsBackNewestFirstPerClient`,
  `theFileOnDiskHoldsNoReadableMailAndIsOwnerOnly` and `aDifferentKeyCannotOpenTheMail`.
- Fetch my mail again: `VaultModel.fetchAgain` (POST `/v1/vault/refetch`); tests
  `VaultTests.VaultRefetchTests`.
- Delivery and retention: `FirraKit/VaultSync.swift` `pull` (store, then acknowledge); tests
  `VaultTests.VaultSyncTests.storesThenAcknowledgesAndFollowsTheCursor`,
  `aFailedAcknowledgementKeepsTheMailForTheNextRun` and `nothingIsAcknowledgedWhenTheLocalWriteFails`.
- Read the whole mail: `VaultModel.localMail(for:)` and `VaultStore.record(sourceRef:occurredAt:)`;
  `FirraApp/DecisionsView.swift` `QuoteView`; test
  `VaultTests.EvidenceMailTests.findsTheMailBehindAQuoteByRefAndDate`.

**Connect AI tools**

- One-time token, clipboard and Claude Code command: `FirraApp/AIToolsView.swift` (`SecretBanner`,
  the command string, `copy(_:secret:)`); `ViewModels.swift` `AITokensModel`; tests
  `APITests.APIClientTests.tokenCreateSendsNameAndDaysAndRevokeAcceptsNoContent` and
  `APITests.ViewModelTests.secretIsShownOnceThenDismissed`.

**Notch pill**

- Toggle and persistence across launches: `FirraApp/MenuBarContent.swift`; `AppModel.notchEnabled`
  stores `notch.enabled` in `UserDefaults`.
- Show/hide and geometry: `AppModel.updateNotch`; `FirraApp/Notch/NotchController.swift`
  (`start`, `stop`, `position`); `FirraKit/NotchGeometry.swift`; tests `NotchGeometryTests`.
- Brief before a meeting: `FirraKit/NotchState.swift` `apply(.tick)` (ten-minute lead) and
  `.meetings`; `FirraKit/NotchModel.swift`; `FirraKit/APIMeetingBriefs.swift`;
  `FirraApp/Notch/NotchMeetingView.swift`; tests `NotchStateTests.leadWindowAndListAreRespected`,
  `earliestMeetingBadgeAndStartBoundary`, `shownMeetingNeverExpandsAgain` and
  `NotchModelTests.startsImmediatelyTicksEveryThirtySecondsAndForwardsEvents`.
- `--demo-notch`: `FirraApp/Notch/NotchDemo.swift` (`#if DEBUG`) and `apps/mac/README.md`, "Notch
  pill".

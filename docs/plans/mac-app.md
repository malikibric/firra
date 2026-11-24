# Mac app — technical strategy (Milestone D)

Decided now so the API (C) fits it; built only after A–C exit.

## Choice: Swift 6 + SwiftUI, native (R-D3)

| Option | For | Against |
|---|---|---|
| **Swift/SwiftUI (chosen)** | real `MenuBarExtra`, `UserNotifications`, Keychain, login item (`SMAppService`), system look and accessibility, small memory footprint for an always-on app, `ASWebAuthenticationSession` for sign-in | second language in the repo; needs Xcode and macOS CI runners; no code sharing with the TS web |
| Electron | shares TS/React | a browser in a window (explicitly unwanted), heavy for an always-on menu bar app, weak native notifications/Keychain |
| Tauri | lighter than Electron | still a webview UI; Rust + TS; menu bar and notifications via plugins |

Shared code between Mac and web is not a goal: the web shrinks to setup pages, and the contract with
the backend is OpenAPI, not shared components.

## Shape

- `apps/mac/` — Xcode project (SwiftUI app target + menu bar extra), minimum macOS 14 (Observation,
  modern `MenuBarExtra`).
- **API client** generated from `packages/contracts` OpenAPI with Apple's `swift-openapi-generator`
  (build plugin); no hand-written DTOs.
- **Auth:** Supabase Auth (email magic link, Google, Microsoft) through `ASWebAuthenticationSession`
  with PKCE and a `firra://auth` callback; access and refresh tokens in the Keychain; refresh
  before expiry; sign-out clears the Keychain item. Contributors never sign in to the app.
- **Data:** the app only calls `/v1`. It caches the last response per screen for instant open and
  offline reading; no local copy of mail or quotes beyond the cached responses, cleared on sign-out.
- **Freshness:** refresh while open and on a timer from the menu bar (default 15 min); an
  `important` item becomes a local notification from the next poll. Server push (APNs) only if
  polling proves too slow.
- **Deep links:** `firra://client/<id>`, `firra://replay/<run>/<client>` from notifications and MCP
  answers.
- **Distribution:** Developer ID signing + notarization, DMG download; updates via Sparkle (needs
  owner OK as a dependency). Mac App Store later if it helps sales (sandboxing review).
- **CI:** GitHub `macos` runner: build, unit tests, UI smoke tests against a stub API.
- **Localization:** English first, String Catalogs from day one (R-D4).

## Owner prerequisites (before D starts)

1. Install Xcode (free) on the development Mac.
2. Apple Developer Program membership (99 USD/year) for signing and notarization.
3. OK for Sparkle (MIT) and `swift-openapi-generator` (Apache 2.0) as dependencies.

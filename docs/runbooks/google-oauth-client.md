# Google OAuth client for Firra (owner task, free, about 15 minutes)

Firra connects to Gmail and Calendar with a **Desktop app** OAuth client. Creating one only produces a client id and secret; it hosts nothing and costs nothing. Button names in the Google Cloud Console change from time to time; follow the intent.

1. Open Google Cloud Console, pick (or create) a project, for example the existing `firra-509914`.
2. **APIs and services, Library:** enable **Gmail API** and **Google Calendar API**.
3. **OAuth consent screen** (Google Auth platform, Branding/Audience):
   - User type **External**, publishing status **Testing** (keep it in Testing for the pilot).
   - App name `Firra`, your support email, developer contact email.
   - **Audience, Test users:** add your own Gmail address and every pilot partner's Gmail address (max 100). Only test users can connect.
4. **Data access (scopes):** add `.../auth/gmail.readonly`, `.../auth/calendar.readonly`, `openid`, `email`. Gmail readonly is a *restricted* scope: fine in Testing for test users, but public launch needs Google's verification (see `docs/research/google-local.md`).
5. **Clients, Create client:** application type **Desktop app**, name `Firra Mac`. Download the JSON (it contains `"installed": {"client_id": …, "client_secret": …}`).
6. Put that file at `~/Library/Application Support/Firra/google-oauth-client.json` on each Mac that runs Firra (mode 0600: `chmod 600 …`). The app reads it from there; it is not part of the DMG. For a partner, send it to them privately or place it during the day-0 call.
7. In Firra: onboarding, **Connect Google**. The browser shows "Google hasn't verified this app": choose Advanced, continue (expected in Testing). In Testing mode the refresh token **expires after 7 days**: Firra asks you to reconnect.

Checks: if the file is missing the app says "Add Google's Desktop client JSON at …"; if your address is not a test user, Google refuses with `access_denied`.

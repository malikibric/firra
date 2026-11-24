# MCP over a link: relay, devices and OAuth (L7-01 design and threat model)

Written 2026-10-05 by the lead. Implemented by L7-03 (relay, Codex), L7-04 (engine side, Codex), L7-02 (OAuth server, lead), tested by L7-06 (Muse Spark). Context: `docs/plans/local-v1.md` D4, T15, T16; `docs/research/mcp-clients.md`
(claude.ai connects to a remote MCP **from Anthropic's cloud**; DCR and CIMD supported; ChatGPT needs a Business/Enterprise/Edu plan with developer mode); `docs/plans/f-mcp-oauth.md` (the authorization plan this builds on).

## 1. Shape

```
claude.ai / ChatGPT ──HTTPS POST /mcp  (Authorization: Bearer <access token>)──▶ relay (apps/api, Cloud Run, EU)
                                                                                   │ finds the user's online device
                                              outbound WebSocket opened by the Mac ◀┘ request frame
Mac engine: validates the token itself, runs the MCP tool, audits, answers ──▶ response frame ──▶ relay ──▶ client
```

- The relay stores **nothing about clients**. It holds device records (public keys), OAuth grants and short-lived routing state in memory. It never logs request or response bodies, tool arguments, or headers other than a counted allow-list.
- One device per user in v1 (a second Mac registers as a new device and replaces the first after confirmation in the app).
- "No device online" is an answer, not an error page: HTTP 200 with JSON-RPC error `firra_offline` ("Firra on your Mac is offline") so the AI tells the user plainly.

## 2. Roles and secrets

| Thing | Where | Notes |
|---|---|---|
| Device key pair (Ed25519) | private key: Keychain slot `device_signing_key`, engine memory; public key: control-plane DB | created by the app at first registration |
| User account | Supabase Auth (EU) | the existing sign-in of the app (`SupabaseAuth.swift`); needed only for MCP over a link |
| OAuth grant | control-plane DB: `grant(id, user_id, device_id, client_id, resource, scope='read', created_at, revoked_at)` | one per connector authorization, bound to the user **and device** |
| Access token | opaque, 15 minutes, hashed in DB; or a JWT with `aud = https://<relay>/mcp`, `sub = user`, `grant = id` | decided by the L7-02 spike (Supabase OAuth server vs ours) |
| Refresh token | rotating, 30-day absolute lifetime, family revoked on reuse | |

## 3. Device registration

1. The app (signed in with Supabase) calls `POST /devices` with the device **public key** and a device name; the relay stores it under the user and returns `device_id` and a one-time `enroll_nonce`.
2. The app signs `enroll_nonce` with the private key and calls `POST /devices/{id}/activate`; the relay verifies the signature and marks the device active. A device that is not activated cannot connect.
3. Revocation: the user removes the device in the app (`DELETE /devices/{id}`); grants bound to it are revoked in the same transaction.

## 4. The device link (WebSocket)

`wss://<relay>/device` ; the engine connects after registration and whenever it starts. Frames are JSON text, at most 1 MiB; larger bodies are split into `chunk` frames. Ping every 20 s from the relay; the engine reconnects with exponential backoff (1 s … 60 s) and jitter.

1. Relay → device `challenge {nonce}` (32 random bytes, base64). Device → relay `hello {device_id, protocol: 1, signature(nonce)}`. The relay verifies against the stored public key; a failure closes the socket (code 4401). (Socket attempts are limited to 30 per IP per minute and refused before the socket is accepted.)
2. Relay → device `request {id, method, path, headers, body_b64, deadline_ms}` with headers limited to the allow-list `authorization, accept, content-type, mcp-session-id, mcp-protocol-version`. The device answers `response {id, status, headers, body_b64, final}` or a sequence of `chunk {id, seq, body_b64, final}` (streaming). `cancel {id}` from either side aborts.
3. At most 8 requests in flight per device; more get `429 busy` from the relay. Every request has a deadline (default 60 s, max 120 s); on expiry the relay answers the client with an MCP error "Firra is not answering" and sends `cancel`.
4. One relay instance in the pilot (stateless routing is not needed). Before public launch the routing table moves to a shared channel (Pub/Sub) so any instance can reach any device.

## 5. Authorization flow (what the client sees)

The relay (resource server) serves `/.well-known/oauth-protected-resource/mcp` naming the authorization server; `401` with `WWW-Authenticate: Bearer resource_metadata="…"` on a missing or bad token. Clients register with DCR or CIMD (`docs/research/mcp-clients.md`), use PKCE,
send `resource=https://<relay>/mcp` in authorization and token requests, and refresh tokens (ChatGPT also needs `offline_access`). The consent page (minimal, served by `apps/api`, no product web app) shows: the client's name, "read-only access to your Firra on <device name>", an approve and a deny button; it needs the user's Supabase session.

**The engine validates the token again.** The relay forwards the `authorization` header untouched. The engine checks issuer, signature (JWKS cached from the control plane; opaque tokens: an introspection call through the relay with the same device channel), audience, expiry, `sub`, and that the grant is bound to **this** device and not revoked
(the relay pushes revocations to the device in a `revoked {grant_id}` frame and the engine also re-checks every 60 s while connected). A relay that is compromised can drop or delay requests but cannot make the engine run a tool for a token it did not issue.

## 6. Threats and mitigations

| Threat | Mitigation |
|---|---|
| Relay compromised (read access to traffic) | v1 pilot: terminating TLS at the relay means it can read tool answers in transit → documented residual risk, retention none, no logging of bodies; **before public launch: passthrough** (TLS terminates on the Mac, the relay routes by SNI/device name and sees only ciphertext). Tools are read-mostly, the engine re-validates tokens, quotes are ≤ 25 words and `untrusted_quote`-marked. |
| Stolen access token | 15-minute lifetime, audience-bound, grant bound to user+device, immediate revocation (device re-check ≤ 60 s, relay-side deny list at once) |
| Stolen refresh token | rotation with reuse detection (family revoked), 30-day absolute limit |
| Rogue device impersonating a user's Mac | device must prove a private key whose public half was activated through the user's Supabase session |
| Replay of frames | `request.id` unique per connection; `challenge` nonce per connection; deadlines |
| Request smuggling / header injection | allow-listed headers, body size caps, JSON-RPC only on `POST /mcp`, `Origin` rejected for non-browser clients (browser MCP clients are out of scope) |
| Prompt injection through returned text | MCP results mark third-party text `untrusted_quote`; descriptions carry the rules; AI-side risk accepted (L4-05 tests) |
| DoS / abuse of the public endpoint | per-IP and per-grant rate limits, bounded in-flight requests, short deadlines, no work done for unauthenticated calls beyond the 401 |
| Device offline hijack of routing | one active device per user; replacing needs a signed-in confirmation in the app |
| Relay logs leaking | logs hold counts, durations, status codes, grant and device ids only; the existing log-hygiene test is extended to the relay (L8-06) |

## 7. Acceptance (L7-06 oracle, L7-07 interop)

Wrong audience, expired, revoked and replayed tokens are refused at the engine even if the relay forwards them; a second user's token never reaches the first user's device; oversize and malformed frames close the socket without crashing the relay; a slow device times out cleanly; an offline device returns `firra_offline`; DCR registration cannot be used to read tools without user consent; `claude.ai` (web, desktop, mobile), Claude Code over the URL and ChatGPT (where the plan allows) connect, call tools and are cut off immediately when the grant is revoked.

## 8. Stage 1 (pilot): link grants, no accounts (lead decision 2026-10-05, owner to confirm before any deploy)

OAuth (sections 2, 3 and 5) needs an authorization server, a device registry tied to user accounts and a consent page; the choice is still being
researched (L7-02). So the relay ships in two stages and the device link (section 4) is the same in both:

- **Stage 1, link grants (this build):** no Supabase accounts, no cloud database. A device identifies itself by an Ed25519 key it
  generates (`device_id` = first 16 hex of sha256 of the public key; first connection registers it, trust on first use; the challenge in
  section 4 proves possession). The owner creates a **connector link** in the Mac app: the engine makes a 256-bit secret, keeps only its
  sha256 (with name and scopes) in a 0600 file, tells the relay the hash over the device link (`grant_add`) and shows the URL
  `https://<relay>/m/<device_id>.<secret>` **once**. Pasting that URL as a custom connector in claude.ai / ChatGPT with "no authentication"
  is the whole setup. The relay keeps grants **in memory only** (the engine re-sends them at every connect). The relay forwards the secret
  untouched; **the engine checks it again** against its own list, so a compromised relay cannot invent a grant. Revoking removes the grant in
  the engine (immediately effective) and the relay. Grants carry scopes (`intelligence`, optionally `sources`, section 5.0 of the source
  layer); the engine enforces them.
- **Risks accepted for the pilot (documented in the consent text):** the link is a bearer secret (anyone with the URL can use it; it can
  appear in the AI vendor's connector settings and logs); mitigations: 256 bits, per-grant revoke, per-grant rate limit, intelligence
  scope by default, the engine's own switch for sources, the relay never logs the path secret.
- **Stage 2, OAuth:** one-click connectors with sign-in and "Allow" (sections 2, 3, 5) replace link grants for claude.ai and ChatGPT when
  L7-02 lands; stage 1 stays for tools that accept a URL only.
- **UNVERIFIED:** that claude.ai custom connectors and ChatGPT developer-mode connectors accept an authless remote MCP URL (docs say
  "no authentication" is an option for ChatGPT; claude.ai to be confirmed with the owner's accounts, L7-07).


**Logging:** Cloud Run writes its own request log, which includes the URL path and so the link secret; `scripts/deploy-relay.sh` adds an exclusion for the relay's request log to the project's `_Default` sink. The relay's own logs hold no secrets. **Plaintext note:** in stage 1 MCP answers (including source excerpts when the owner allows them) pass through the relay in memory over TLS; it stores nothing, but it is not an encrypted passthrough (that is the v1.1 item).

**Deploy (L7-08, not yet run):** `scripts/deploy-relay.sh --yes` builds `services/relay/Dockerfile` with Cloud Build and deploys one Cloud Run
instance (min and max 1 because grants live in memory; 256 Mi, request timeout 3600 s, the Mac reconnects when a socket ends). It needs the
owner's go and `FIRRA_GCP_PROJECT`; the address it prints is what the owner pastes into Firra's "Relay address" field. The relay holds no
secret and no data; its only configuration is `PORT`.

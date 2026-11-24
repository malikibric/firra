# Research S4a: MCP client matrix (L0-05)

Date read: 2026-10-04. Tags: CONFIRMED = primary vendor source verbatim. UNVERIFIED = secondary/inferred/not found. Read first: `docs/research/mcp.md`, `docs/plans/f-mcp-oauth.md`, `apps/api/src/mcp/`, `docs/runbooks/mcp-partner-guide.md` (existing PAT + tools surveyed in clone).

## Matrix (one page)

| Client | Remote URL | Plan needed | OAuth (DCR/CIMD/PKCE/refresh) | Transport | Local stdio | One-click install | Tools/prompts/resources/sampling | Directory | Offline behaviour | Read-only/third-party-text guidance |
|---|---|---|---|---|---|---|---|---|---|---|
| claude.ai web | CONFIRMED | CONFIRMED Free/Pro/Max/Team/Enterprise; Free max 1 | CONFIRMED DCR + CIMD, redirect `https://claude.ai/api/mcp/auth_callback`, refresh | CONFIRMED Streamable HTTP (SSE legacy) | n/a (remote only) | CONFIRMED URL in Connectors settings | Tools CONFIRMED; prompts/resources/sampling UNVERIFIED | CONFIRMED Connectors Directory + verification | UNVERIFIED (no vendor statement found) | UNVERIFIED (general caution only) |
| Claude mobile iOS/Android | CONFIRMED (same infra backs all) | CONFIRMED same plans | CONFIRMED same as web | CONFIRMED same | n/a | CONFIRMED same connector, mobile inherits | UNVERIFIED per-feature | CONFIRMED same directory | UNVERIFIED | UNVERIFIED |
| Claude Desktop | CONFIRMED remote + local | CONFIRMED same plans | CONFIRMED same | CONFIRMED both | CONFIRMED `claude_desktop_config.json` + `.mcpb` | CONFIRMED config file + directory | UNVERIFIED per-feature | CONFIRMED same | UNVERIFIED cache cards | UNVERIFIED |
| ChatGPT web/desktop | CONFIRMED remote only (tunnel for private) | CONFIRMED Business/Enterprise/Edu beta; admin must enable developer mode | CONFIRMED OAuth, needs refresh tokens + `offline_access` | CONFIRMED remote MCP (Streamable HTTP) | CONFIRMED NOT directly ("Not directly") | CONFIRMED Settings → Plugins / Apps → Create + Scan Tools | Tools CONFIRMED; write needs custom app (OpenAI-built = search-only) | CONFIRMED plugin submission portal | UNVERIFIED | CONFIRMED "malicious server can exfiltrate" warning |
| Claude Code | CONFIRMED `--transport http` | n/a (user's subscription/CLI) | CONFIRMED OAuth via `/mcp`, DCR path | CONFIRMED http/sse/stdio; SSE deprecated | CONFIRMED stdio | CONFIRMED `claude mcp add ...` | Tools CONFIRMED; rest UNVERIFIED | n/a (uses directory connectors via same infra) | UNVERIFIED | UNVERIFIED |
| Codex CLI | CONFIRMED Streamable HTTP + stdio | n/a | CONFIRMED CIMD preferred, DCR fallback, PKCE loopback | CONFIRMED both | CONFIRMED stdio | CONFIRMED `codex mcp add` + `config.toml` | CONFIRMED `instructions` field read | n/a | UNVERIFIED | UNVERIFIED |
| Cursor | CONFIRMED http/SSE | n/a (IDE license) | CONFIRMED OAuth (DCR + static creds), fixed callbacks | CONFIRMED stdio/SSE/Streamable HTTP | CONFIRMED stdio | CONFIRMED `cursor://.../mcp/install?name=&config=` (base64) | CONFIRMED Apps extension UI | CONFIRMED Marketplace + cursor.directory | UNVERIFIED | UNVERIFIED |
| VS Code | CONFIRMED http/SSE/stdio | n/a | CONFIRMED OAuth example configs | CONFIRMED all | CONFIRMED stdio | CONFIRMED `vscode:mcp/install?{json}` + `--add-mcp` + autodiscovery | Tools CONFIRMED; rest UNVERIFIED | CONFIRMED curated list / marketplace ext | UNVERIFIED | UNVERIFIED |

## Per-client details

### 1. claude.ai web
- Quote: "Custom connectors using remote MCP are available on Claude, Cowork, and Claude Desktop for users on Free, Pro, Max, Team, and Enterprise plans. Free users are limited to one custom connector." — URL: https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp — 2026-10-04 — CONFIRMED
- Quote: "When you add a custom connector, Claude connects to your remote MCP server from Anthropic's cloud infrastructure, rather than from your local device. This is true across every Claude client, including claude.ai, Claude Desktop, Cowork, and the mobile apps." — same URL — CONFIRMED
- Quote: "A transport is how Claude and your server exchange MCP messages. Use Streamable HTTP, which the MCP specification defines for remote servers. Claude also supports the legacy HTTP+SSE transport, which is being deprecated in favor of Streamable HTTP." — URL: https://claude.com/docs/connectors/building — 2026-10-04 — CONFIRMED
- Quote: "Claude follows the 2025-03-26, 2025-06-18, and 2025-11-25 authorization specifications" + "Claude can register itself with your authorization server through Dynamic Client Registration (DCR)." + "Redirect URI: allow https://claude.ai/api/mcp/auth_callback for the hosted surfaces and a loopback redirect for Claude Code" + "Claude refreshes access tokens when they expire." — same URL — CONFIRMED
- Quote: "oauth_dcr | OAuth 2.0 with Dynamic Client Registration (RFC 7591) | Supported by default" / "oauth_cimd | OAuth 2.0 with Client ID Metadata Document | Supported by default" — URL: https://claude.com/docs/connectors/building/authentication — 2026-10-04 — CONFIRMED
- Add steps: Quote: "Navigate to Customize > Connectors / Click + then Add custom connector / Add your connector's remote MCP server URL / Optionally, click Advanced settings to specify an OAuth Client ID and OAuth Client Secret" — support URL above — CONFIRMED. Team/Enterprise variant requires Owner via Organization settings > Connectors. CONFIRMED same source.
- Request size/timeout numbers: UNVERIFIED (building page mentions "size and timeout limits" but no numbers captured; settle in S4 interop test).
- Offline/caching: UNVERIFIED (no vendor statement found; test: stop relay, call tool, record message + whether tool list persists).

### 2. Claude mobile (iOS/Android)
- Same connector infrastructure backs all surfaces: Quote: "Claude connects to it from every Claude app, and a remote server is the recommended kind for a directory listing." — https://claude.com/docs/connectors/building — CONFIRMED. Mobile-specific OAuth redirect/plan gating differences: UNVERIFIED. Test in L7-07 on iOS + Android with the relay URL.
- Plan/beta: inherits web plans above (CONFIRMED for availability across clients; per-plan mobile restriction UNVERIFIED).

### 3. Claude Desktop
- Remote custom connectors available on Desktop (see web quote "available on Claude, Cowork, and Claude Desktop"). CONFIRMED.
- Local servers are a separate mechanism: Quote: "Local MCP servers configured in Claude Desktop via claude_desktop_config.json are a separate mechanism and do use your local network, but those aren't available in Cowork or claude.ai." — support URL — CONFIRMED.
- Desktop extensions: Quote: "To package one for the Claude desktop app, see Build a desktop extension with MCPB." + "A local server runs on the user's computer." — building URL — CONFIRMED (format/manifest fields UNVERIFIED here; settle from MCPB guide during F4/F5).
- Config location: `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS — UNVERIFIED in this pass (widely documented secondary; confirm from Claude Desktop MCP docs during implementation).

### 4. ChatGPT (web + desktop; Apps/Connectors, developer mode)
- Quote: "full MCP support, and developer mode are available for ChatGPT Business and Enterprise/Edu customers on ChatGPT web. Admins/owners can enable developer mode in workspace settings, create and test custom apps, and publish them for their workspace." — URL variant: https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt (fetched via .svgz mirror 2026-10-04) — CONFIRMED (plan gate + beta).
- Quote: "Workspace admins must first enable developer mode from their Admin workspace settings. The developer mode toggle can be found in Workspace Settings → Permissions & Roles → Connected Data Developer mode / Create custom MCP connectors." — same — CONFIRMED.
- Quote: "Can I connect to a local MCP server? Not directly. ChatGPT connects to remote MCP servers. If your MCP server runs on a private network, on-premises, or on a developer machine, use Secure MCP Tunnel" — same — CONFIRMED (so Firra relay is the only ChatGPT path; stdio is not a ChatGPT option).
- Quote: "If using OAuth for authentication ... ensure your OAuth/OpenID Connect provider is set up to provide refresh tokens ... Check [that] scopes_supported [lists] offline_access ... If OAuth is set up without offline_access, ... ChatGPT may lose access after the initial authorization expires." — same — CONFIRMED (relay AS must advertise `offline_access`-equivalent refresh scope behaviour).
- Steps: "Provide the endpoint and required metadata / Pick the authentication mechanism / Click Scan Tools and wait / Click Create / app appears as draft in Workspace Settings → Apps → Drafts" — CONFIRMED same source.
- OpenAI-built apps are search-only today; custom MCP apps needed for write: UNVERIFIED verbatim in this pass (secondary gist states "OpenAI-built apps are search-only today and do not support write actions. Use custom MCP apps for write/modify" — treat UNVERIFIED until help-center confirmation).
- Desktop + mobile: Quote: "Mobile clients – once the app is linked on ChatGPT web, it will be available on ChatGPT mobile apps as well." — URL: https://learn.chatgpt.com/apps-sdk/deploy/connect-chatgpt — 2026-10-04 — CONFIRMED (mobile inherits web link).
- Security: Quote: "A malicious server can exfiltrate sensitive data from anything that enters the model's context." — URL: https://developers.openai.com/api/docs/guides/tools-remote-mcp — 2026-10-04 — CONFIRMED (quote for prompt-injection posture).
- Directory: Quote: "when you are ready for public distribution, submit the plugin through the plugin submission portal." — learn.chatgpt.com URL — CONFIRMED (review criteria UNVERIFIED).
- Agent mode + deep research limits with custom apps: UNVERIFIED (Malayalam help excerpt mentions agent mode doesn't use custom apps / deep research read-only — machine-translated, treat UNVERIFIED).

### 5. Claude Code
- Quote: "claude mcp add --transport http <name> <url>" / "Example with Bearer token: claude mcp add --transport http secure-api https://api.example.com/mcp --header \"Authorization: Bearer your-token\"" — URL: https://code.claude.com/docs/en/mcp — 2026-10-04 — CONFIRMED.
- Quote: "The SSE (Server-Sent Events) transport is deprecated. Use HTTP servers instead, where available." — same — CONFIRMED.
- Quote: "When configuring MCP servers via JSON in .mcp.json, ~/.claude.json, or claude mcp add-json, the type field accepts streamable-http as an alias for http." — same — CONFIRMED.
- Quote: "A JSON entry that has a url but no type is a configuration error, because Claude Code reads an entry with no type as a stdio server." — same — CONFIRMED (implementation gotcha for our docs).
- stdio: Quote: "claude mcp add [options] <name> -- <command> [args...]" — same — CONFIRMED. OAuth via `/mcp` in-session + DCR path: UNVERIFIED verbatim here (secondary bundles doc lists `claude mcp add` OAuth flow; confirm with `claude mcp --help` in L3-08/L4).
- Config files: `.mcp.json` (project), `~/.claude.json` (user) — CONFIRMED via same page (exact precedence UNVERIFIED).

### 6. Codex CLI
- Quote: "Codex stores MCP configuration in `config.toml` alongside other Codex configuration settings. By default this is `~/.codex/config.toml`, but you can also scope MCP servers to a project with `.codex/config.toml` (trusted projects only)." — URL: https://developers.openai.com/codex/mcp — 2026-10-04 — CONFIRMED.
- Quote: "Configure each MCP server with a `[mcp_servers.<server-name>]` table" / "STDIO servers ... Streamable HTTP servers ... Bearer token authentication OAuth authentication, including Client ID Metadata Documents (CIMD) and Dynamic Client Registration (DCR)" — same — CONFIRMED.
- Quote: "By default, Codex automatically chooses CIMD when the authorization server advertises `client_id_metadata_document_supported: true`, includes `none` in `token_endpoint_auth_methods_supported`, and the callback uses a supported loopback URL. Otherwise, Codex uses DCR when available. A configured OAuth client ID always takes precedence" — same — CONFIRMED (this is the minimum-OAuth contract: advertise CIMD + `none` + loopback, or DCR).
- Quote: "If the MCP server advertises `scopes_supported`, Codex prefers those server-advertised scopes during OAuth login." — same — CONFIRMED (relay metadata must list `read`).
- Commands: `codex mcp add <name> --env ... -- <cmd>`, `codex mcp list`, `codex mcp login <server-name>` — CONFIRMED same page (exact flags UNVERIFIED until `--help` in L0-06).
- `instructions` field honoured server-wide — CONFIRMED same page. Sampling/elicitation/prompts support: UNVERIFIED.

### 7. Cursor
- Quote: "Cursor supports three transport methods: stdio | SSE | Streamable HTTP" + "Cursor supports the MCP Apps extension." — URL: https://cursor.com/en-US/docs/mcp — 2026-10-04 — CONFIRMED.
- Quote: "cursor://anysphere.cursor-deeplink/mcp/install?name=$NAME&config=$BASE64_ENCODED_CONFIG" — URL: https://cursor.com/docs/mcp/install-links — 2026-10-04 — CONFIRMED. Config = base64 JSON of mcp.json entry. CONFIRMED.
- Quote: "Cursor uses fixed OAuth redirect URLs for MCP servers. Register the callback: https://www.cursor.com/agents/mcp/oauth/callback http://localhost:8787/callback" — cursor docs page (via en-US docs fetch) — 2026-10-04 — CONFIRMED (allowlist both).
- Config locations: Quote table rows: "Cursor (global) ~/.cursor/mcp.json / Cursor (workspace) <workspace>/.cursor/mcp.json" — URL: https://code.visualstudio.com/docs/agents/reference/mcp-configuration — 2026-10-04 — CONFIRMED (Microsoft page surveying Cursor paths).
- Static OAuth creds supported when DCR unavailable — CONFIRMED same Cursor docs (exact `auth` object shape UNVERIFIED here; copy from docs during F4).

### 8. VS Code
- Quote: "VS Code provides a URL handler for installing an MCP server from a link: vscode:mcp/install?{json-configuration}" — URL: https://code.visualstudio.com/api/extension-guides/ai/mcp — 2026-10-04 — CONFIRMED.
- Quote: "Workspace configuration: Specify the server configuration in a .vscode/mcp.json file / Global configuration: Define servers globally in the user profile / Autodiscovery: VS Code can discover servers from other tools like Claude Desktop / Command line: Install MCP servers from the command line with the --add-mcp" — same — CONFIRMED.
- Config reference with stdio + HTTP + OAuth examples lives at https://code.visualstudio.com/docs/agents/reference/mcp-configuration — CONFIRMED (exact schema to copy during F4; Copilot-plan gating UNVERIFIED).

## Features (tools/prompts/resources/sampling/elicitation)
- Tools work everywhere we checked (CONFIRMED per client above). Prompts-as-slash-commands, resources, sampling, elicitation per client: UNVERIFIED in this pass (vendor docs describe support unevenly; our server should assume tools-only and treat the rest as progressive enhancement). Settle with one tools-only probe per client in S4 interop (L7-07 covers hosted; local probes cover Code/Codex/Cursor/VS Code/Desktop).

## Directory listing
- Anthropic: Connectors Directory; Quote: "Browse the Connectors Directory to discover third-party MCP servers" + "a remote server is the recommended kind for a directory listing" — CONFIRMED (review/privacy/domain criteria UNVERIFIED; contact mcp-review@anthropic.com for non-default auth types per authentication page).
- OpenAI: plugin submission portal (CONFIRMED above; criteria UNVERIFIED).
- Cursor Marketplace / cursor.directory and VS Code curated list/extension: CONFIRMED existence, criteria UNVERIFIED.

## Behaviour when unreachable (Mac off)
- No vendor documents the exact UX in the fetched pages: UNVERIFIED for all eight. Our contract: relay returns a clear MCP error ("Firra Mac is offline") and clients surface the tool error. Whether each client caches tool lists, retries, or greys the connector must be recorded in S4 interop with relay stopped + Mac-off traces.

## Security guidance (third-party text, read-only)
- OpenAI CONFIRMED exfiltration warning above; our posture (tool descriptions mark `untrusted_quote`, read-only tools, confirmations for writes) aligns with `docs/research/mcp.md` §Consequences. Anthropic/Cursor/VS Code/Codex equivalent guidance pages: UNVERIFIED in this pass (search during F4 tool-description review).

## Recommendations for Firra (minimum OAuth to work with all)
1. Streamable HTTP on one public URL + legacy SSE tolerance (Claude still lists SSE; Codex/Cursor/VS Code accept both). CONFIRMED need.
2. Protected-resource metadata + 401 `WWW-Authenticate` with `resource_metadata`, `resource=https://<api>/mcp` binding, audience-validated tokens, `read` scope advertised. CONFIRMED need (Codex `scopes_supported` + Claude DCR/CIMD + ChatGPT `offline_access` refresh).
3. Support BOTH CIMD and DCR (Claude default DCR, Codex prefers CIMD when advertised). Advertise `client_id_metadata_document_supported: true`, `token_endpoint_auth_methods_supported: [none]`, loopback callback support. CONFIRMED need.
4. Allowlist redirect URIs: `https://claude.ai/api/mcp/auth_callback`, `https://www.cursor.com/agents/mcp/oauth/callback`, `http://localhost:8787/callback`, `http://127.0.0.1:<port>` + loopback range for Codex/Claude Code, VS Code handler callback (UNVERIFIED exact string — capture in interop). ChatGPT web callback: UNVERIFIED exact string (capture during first ChatGPT link test).
5. Refresh with rotation + immediate revocation; advertise refresh support so ChatGPT does not lose access (CONFIRMED need from OpenAI quote).
6. Static PAT header fallback for local stdio + pre-OAuth interop (existing PAT already; keep per f-mcp-oauth D2). CONFIRMED useful.
7. Client order: Claude Code + Codex CLI + Desktop stdio first (no relay dependency), then claude.ai web, then Cursor/VS Code deeplinks, then ChatGPT (gated beta + admin toggle = slowest). Mobile inherits web link.
8. Corrections to `f-mcp-oauth.md`: (a) it lists DCR-vs-CIMD as UNVERIFIED — now CONFIRMED both required; (b) it lacks the concrete redirect allowlist above — add it; (c) ChatGPT `offline_access`/refresh advertisement is a hard gate — add to acceptance; (d) SSE cannot be dropped while Claude lists legacy support — keep dual-era transport.

## Facts only settleable by trying (S4 interop tests)
1. Exact ChatGPT web OAuth callback URL + approval-loop behaviour → test: create developer-mode app against staging relay, record callback + 401 challenge round-trip.
2. claude.ai DCR vs static client-ID path for our AS → test: add relay URL as custom connector, record metadata fetch + registration + `https://claude.ai/api/mcp/auth_callback` exchange.
3. Tool-list caching + offline message per client → test: stop relay/Mac, call tool in each client, screenshot message; restart, confirm recovery without re-adding.
4. Timeout/request-size ceilings per client → test: slow tool (sleep 30/60/120s) + large result (100KB/1MB), record truncation/timeout per client.
5. Prompts/resources/sampling/elicitation rendering per client → test: expose one prompt + one resource + elicitation probe, record UI per client.
6. Cursor/VS Code static-OAuth vs DCR path + exact `auth` JSON → test: install via deeplink + `vscode:mcp/install`, record token exchange.
7. Directory review criteria (both vendors) → test: submit listing applications, record requirements.

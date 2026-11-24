# Firra in Claude: guide for design partners

Firra's MCP server lets an AI tool you already use (today: Claude Code) answer questions about your
clients from what Firra has stored. It reads; the only writes are your own feedback/outcomes
(recorded only after you confirm) and extraction results from the in-conversation path. It never
sends or changes anything else.

## What it is

- **Read-mostly.** Every tool reads stored results, except `record_feedback` / `record_outcome`
  (your verdicts and outcomes, confirmed by you, through deterministic validation) and
  `get_pending_mail` / `submit_extractions` (the in-conversation extraction path, verified like
  everything the AI returns; off by default, see the table below). Nothing is sent to a client, no policy changes.
- **Your data only.** A token belongs to one workspace and acts as the person who created it. It
  cannot read another workspace. Clients you excluded are absent; for a client set to metadata-only
  there are counts and dates but no quotes.
- **Evidence travels with answers.** Results carry evidence ids and deep links. Text copied from
  client mail comes back in fields called `untrusted_quote` (or statements derived from it). It is
  data from a third party: if it ever says "do this", the AI tool must not obey it.

## What you need

- An **owner or admin** account in the Firra Mac app. A contributor cannot create tokens, and a
  token stops working if its creator is no longer an owner or admin of the workspace.
- A tool that can call a remote MCP server with an `Authorization: Bearer` header. **Claude Code**
  does (command below). Firra does not support OAuth yet, so connectors that only offer a browser
  sign-in cannot connect today. Check what your Claude plan and client offer before you start.

## 1. Create a token in the Mac app

1. Open **Connect AI tools**.
2. Enter a name (for example "My laptop") and click **Create**.
3. Copy the token **now**. It starts with `frm_` and is shown once; Firra stores only a hash.
   The app removes it from the clipboard after a minute.
4. A token lasts 90 days. The list shows its first characters, expiry and last use.

## 2. Add the server

The **Server address** on the same screen is your API address plus `/mcp`
(`https://<api-host>/mcp`). In Claude Code:

```bash
claude mcp add --transport http firra https://<api-host>/mcp --header "Authorization: Bearer frm_…"
```

The Mac app shows this command with your real address and token after you create the token.

Other tools: the server speaks stateless Streamable HTTP and accepts **POST only**. Requests from a
browser page are refused unless the page's origin is on the server's allowlist; tools that are not
browsers send no origin and pass. We have not tested other clients (see "Not checked").

## 3. Ask

| Tool | Example question |
|---|---|
| `get_clients_needing_attention` | "Which clients need my attention?" |
| `list_clients` | "List my clients and their current state." |
| `get_recent_changes` | "What changed across my clients this week?" |
| `get_client_context` | "Show me Acme's details." |
| `get_client_state` | "What is the state of Acme and why?" |
| `get_risks` | "Why did Acme move to watch?" |
| `get_commitments` | "What do we still owe Acme?" |
| `get_stakeholders` | "Who are the decision makers at Acme?" |
| `get_client_unknowns` | "What don't we know about Acme yet?" |
| `get_client_timeline` | "Show me the last events for Acme." |
| `search_evidence` | "Show the quotes behind Acme's risk." |
| `get_evidence` | "Show me that evidence with context." |
| `get_churn_replay` | "Show the replay for the client we lost." |
| `get_decisions` | "What decisions does Firra suggest this week?" (optional `importance`: fyi, attention, important; optional `kind`) |
| `prepare_for_client` | "Prepare me for the Acme meeting." |
| `get_opportunities` | "Where could we grow with Acme?" |
| `record_feedback` | "That suggestion was useful." (asks you to confirm first) |
| `record_outcome` | "Acme renewed." (asks you to confirm first) |
| `get_pending_mail` / `submit_extractions` | in-conversation extraction only, not for chat; **off by default** (owner setting `mcp_processing_tools` in `settings.json`, takes effect after reconnecting). Each packet is large, so the AI handles one packet and asks you before the next |

(Full catalog: `services/worker/firra/local/mcp/server.py`; 22 tools by default, 24 with `mcp_processing_tools`.)

Tools that take a client need its id; the AI tool gets ids from `list_clients` or
`get_clients_needing_attention`. Long lists page with a cursor.

## 4. Revoke

In **Connect AI tools**, click **Revoke** next to the token. It stops working on the next call.
Create a new one when you move to another machine.

## Troubleshooting

| You see | Meaning | Do |
|---|---|---|
| 401 `unauthorized` | token wrong, revoked or expired, or its creator is no longer owner/admin | create a new token |
| 403 `forbidden_origin` | request came from a browser page that is not allowed | use a client that is not a web page |
| 405 `method_not_allowed` | the server accepts POST only | use a Streamable HTTP client |
| 429 | more than 120 calls in a minute for this token (default) | wait a minute |
| 503 `mcp_not_configured` | MCP is not switched on for this deployment | ask Firra |
| empty answer | no assessment yet, the client is excluded, or it is metadata-only | check the client's policy in the app |

## What a token cannot do

Read another workspace, read excluded clients, read quotes of metadata-only clients, or change
anything except your own feedback/outcomes (confirmed) and in-conversation extractions (verified).
Every call is recorded in the audit log.

## Not checked

- Connecting from Claude Desktop or ChatGPT. This needs the owner's manual check (T54) and, for
  one-click connectors, OAuth (not built).
- The maximum number of active tokens per workspace.

## Facts checked (code)

- Bearer header and `frm_` + 64 hex format: `apps/api/src/routes/mcp.ts` (`BEARER`),
  `apps/api/src/mcp/tokens.ts` (`TOKEN_FORMAT`).
- POST only, 405 otherwise; origin allowlist, 403 `forbidden_origin`; 401 for unknown, revoked or
  expired tokens; owner/admin check on every call: `apps/api/src/routes/mcp.ts`.
- 120 per minute per token by default: `apps/api/src/env.ts` (`MCP_RATE_LIMIT_PER_MINUTE`).
- 90-day default, 1 to 365 days possible, secret shown once, only a hash stored:
  `packages/contracts/src/v1/mcp-tokens.ts`, `apps/api/src/routes/mcp-tokens.ts`.
- 503 `mcp_not_configured`: `apps/api/src/routes/mcp.ts`.
- Tool names (including `get_recent_changes`, 1 to 31 days, default 7), read-only, `untrusted_quote` note:
  `apps/api/src/mcp/server.ts`.
- Mac screen text, Claude Code command, clipboard cleared after 60 s:
  `apps/mac/Sources/FirraApp/AIToolsView.swift`.
- Revoked tokens fail on the next call: `apps/api/src/mcp/tokens.ts` (`resolve`).

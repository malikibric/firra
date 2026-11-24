# MCP research (2026-10-02)

Facts checked against modelcontextprotocol.io and npm on 2026-10-02. Re-verify before building
(docs/connectors.md rule): the protocol changed in July 2026.

## Protocol

- Latest revision **2026-07-28**. Streamable HTTP is now stateless: one endpoint, every JSON-RPC
  request is its own POST, no `initialize` handshake, no `Mcp-Session-Id`, **no GET stream**,
  no resumable SSE. Each request carries `MCP-Protocol-Version`, `Mcp-Method` (and `Mcp-Name`
  for `tools/call`) headers that must match the body (`400` + code `-32020` on mismatch);
  protocol version and client info travel in `_meta` of the body.
- Servers **MUST** validate `Origin` (403 when present and invalid), **SHOULD** authenticate every
  connection. Server-to-client requests are replaced by multi round-trip requests.
- Older revisions (2025-03-26 … 2025-11-25) used sessions, GET streams and `initialize`. Real
  clients (Claude, ChatGPT) are expected to lag behind the newest revision, so the server must
  speak both eras. A server that supports only the new revision answers GET/DELETE with 405 and
  ignores session headers.
- Authorization is optional in the spec. When used for HTTP: the MCP server is an **OAuth 2.1
  resource server** with Protected Resource Metadata (RFC 9728) and `WWW-Authenticate` on 401; the
  client uses Client ID Metadata Documents (preferred) or dynamic registration (deprecated),
  PKCE, the `resource` parameter (RFC 8707); the server must validate that a token was issued for
  **it** (audience) and must not accept or pass through other tokens; tokens never in the query
  string; 401 for invalid or expired tokens, 403 `insufficient_scope` for scope problems.

## SDK

- `@modelcontextprotocol/sdk` 1.31.0 is the v1 line (legacy eras). The **v2 line** is split into
  scoped packages: `@modelcontextprotocol/server` 2.2.0, `@modelcontextprotocol/core`,
  `@modelcontextprotocol/hono` 2.0.1 (fits our Hono API), `@modelcontextprotocol/node`,
  `@modelcontextprotocol/client`, plus `@modelcontextprotocol/conformance` (spec test suite) and
  `@modelcontextprotocol/inspector`. v2 release notes mention 2026-07-28 features. Whether v2 also
  serves the 2025-11-25 era must be proved by a test with a v1 client before choosing (see plan).
- Both lines depend on zod (compatible with our zod 4).

## Consequences for Firra

1. Stateless suits Cloud Run (min instances 0, no sticky sessions): no session store.
2. A static **personal access token** is a pragmatic first step for design partners (the spec
   leaves auth optional); a directory listing needs the OAuth flow, so the token model must not
   prevent it (tokens bound to a user and a workspace, hashed, scoped, revocable).
3. Tool results contain third-party text (client quotes). The tool description and result
   structure must mark it as data from external parties, never instructions (prompt-injection
   defence; CLAUDE.md §6).

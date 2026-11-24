/**
 * Microsoft identity platform (v2, authorization code + PKCE) for Outlook mail/calendar connections.
 * Docs checked: authorize endpoint (tenant `organizations` for work/school accounts only,
 * response_mode=query, prompt=select_account, login_hint), token endpoint (code redemption with
 * client_secret + code_verifier; refresh_token only with offline_access; `scope` lists what was
 * granted), ID token claims (iss per tenant, aud, tid, oid). The mailbox address comes from Graph
 * `GET /me` (`mail`, else `userPrincipalName`), not from optional ID token claims.
 */
import { z } from "zod";
import { codeChallenge } from "./pkce.js";

const GRAPH = "https://graph.microsoft.com";
export const MAIL_SCOPE = `${GRAPH}/Mail.Read`;
export const CALENDARS_SCOPE = `${GRAPH}/Calendars.Read`;
export const MICROSOFT_SCOPES = [
  "openid",
  "email",
  "profile",
  "offline_access",
  MAIL_SCOPE,
  CALENDARS_SCOPE,
] as const;

export interface MicrosoftOAuthConfig {
  clientId: string;
  clientSecret: string;
  redirectUri: string;
  /** `organizations` (default), `common`, or one tenant id. */
  tenant: string;
}

const authority = (tenant: string) =>
  `https://login.microsoftonline.com/${encodeURIComponent(tenant)}/oauth2/v2.0`;

export function microsoftAuthorizationUrl(
  config: MicrosoftOAuthConfig,
  options: { state: string; verifier: string; loginHint?: string },
): string {
  const url = new URL(`${authority(config.tenant)}/authorize`);
  url.search = new URLSearchParams({
    client_id: config.clientId,
    redirect_uri: config.redirectUri,
    response_type: "code",
    response_mode: "query",
    scope: MICROSOFT_SCOPES.join(" "),
    prompt: "select_account",
    state: options.state,
    code_challenge: codeChallenge(options.verifier),
    code_challenge_method: "S256",
    ...(options.loginHint ? { login_hint: options.loginHint } : {}),
  }).toString();
  return url.toString();
}

const tokenResponse = z.object({
  access_token: z.string().min(1),
  refresh_token: z.string().min(1).optional(),
  id_token: z.string().min(1),
  scope: z.string(),
});

const idTokenClaims = z.object({
  iss: z.string(),
  aud: z.string(),
  tid: z.string().regex(/^[0-9a-f-]{36}$/),
  oid: z.string().min(1),
});

const me = z.object({
  id: z.string().min(1),
  mail: z.string().nullish(),
  userPrincipalName: z.string().nullish(),
});

export interface MicrosoftGrant {
  refreshToken: string;
  tenantId: string;
  objectId: string;
  email: string;
  scopes: string[];
}

export class MicrosoftGrantError extends Error {
  constructor(
    readonly reason: "exchange_failed" | "no_refresh_token" | "missing_scope" | "no_mailbox",
  ) {
    super(reason);
    this.name = "MicrosoftGrantError";
  }
}

/** Granted scopes as full Graph URIs ("Mail.Read" and "https://graph.microsoft.com/mail.read" alike). */
export function normalizeScopes(scope: string): string[] {
  const known = new Map(MICROSOFT_SCOPES.map((s) => [s.toLowerCase(), s]));
  return scope
    .split(" ")
    .filter(Boolean)
    .map((s) => {
      const lower = s.toLowerCase();
      return known.get(lower) ?? known.get(`${GRAPH}/${lower}`) ?? s;
    });
}

export async function exchangeMicrosoftCode(
  config: MicrosoftOAuthConfig,
  code: string,
  verifier: string,
  fetchImpl: typeof fetch = fetch,
): Promise<MicrosoftGrant> {
  const response = await fetchImpl(`${authority(config.tenant)}/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      code,
      code_verifier: verifier,
      client_id: config.clientId,
      client_secret: config.clientSecret,
      redirect_uri: config.redirectUri,
      scope: `offline_access ${MAIL_SCOPE} ${CALENDARS_SCOPE}`,
    }),
    signal: AbortSignal.timeout(15_000),
  });
  if (!response.ok) throw new MicrosoftGrantError("exchange_failed");
  const body = tokenResponse.safeParse(await response.json());
  if (!body.success) throw new MicrosoftGrantError("exchange_failed");
  if (!body.data.refresh_token) throw new MicrosoftGrantError("no_refresh_token");

  // Received directly from the token endpoint over TLS (OpenID Connect Core 3.1.3.7): no signature
  // check needed; audience and tenant-bound issuer are still validated.
  const payload = body.data.id_token.split(".")[1] ?? "";
  const claims = idTokenClaims.safeParse(
    JSON.parse(Buffer.from(payload, "base64url").toString("utf8")),
  );
  if (
    !claims.success ||
    claims.data.aud !== config.clientId ||
    claims.data.iss !== `https://login.microsoftonline.com/${claims.data.tid}/v2.0`
  )
    throw new MicrosoftGrantError("exchange_failed");

  const scopes = normalizeScopes(body.data.scope);
  if (!scopes.includes(MAIL_SCOPE)) throw new MicrosoftGrantError("missing_scope");

  const profile = await fetchImpl(`${GRAPH}/v1.0/me?$select=id,mail,userPrincipalName`, {
    headers: { Authorization: `Bearer ${body.data.access_token}` },
    signal: AbortSignal.timeout(15_000),
  });
  if (!profile.ok) throw new MicrosoftGrantError("exchange_failed");
  const user = me.safeParse(await profile.json());
  if (!user.success || user.data.id !== claims.data.oid)
    throw new MicrosoftGrantError("exchange_failed");
  const email = z
    .email()
    .safeParse((user.data.mail ?? user.data.userPrincipalName ?? "").toLowerCase());
  if (!email.success) throw new MicrosoftGrantError("no_mailbox");

  return {
    refreshToken: body.data.refresh_token,
    tenantId: claims.data.tid,
    objectId: claims.data.oid,
    email: email.data,
    scopes,
  };
}

/** Admin consent for application permissions, pinned to one (already verified) tenant. */
export function microsoftAdminConsentUrl(
  config: MicrosoftOAuthConfig,
  options: { tenantId: string; state: string; redirectUri: string },
): string {
  const url = new URL(
    `https://login.microsoftonline.com/${encodeURIComponent(options.tenantId)}/v2.0/adminconsent`,
  );
  url.search = new URLSearchParams({
    client_id: config.clientId,
    scope: `${GRAPH}/.default`,
    redirect_uri: options.redirectUri,
    state: options.state,
  }).toString();
  return url.toString();
}

const appTokenResponse = z.object({ access_token: z.string().min(1) });
export type MailboxProbe = { ok: true } | { ok: false; reason: "not_found" | "consent_missing" };

/** Client-credentials token for Graph in the customer's tenant; null when consent is missing. */
export async function microsoftAppToken(
  config: MicrosoftOAuthConfig,
  tenantId: string,
  fetchImpl: typeof fetch = fetch,
): Promise<string | null> {
  const response = await fetchImpl(`${authority(tenantId)}/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "client_credentials",
      client_id: config.clientId,
      client_secret: config.clientSecret,
      scope: `${GRAPH}/.default`,
    }),
    signal: AbortSignal.timeout(15_000),
  });
  if (response.status === 400 || response.status === 401) return null;
  if (!response.ok) throw new Error(`app token failed: ${String(response.status)}`);
  return appTokenResponse.parse(await response.json()).access_token;
}

/**
 * Checks that the app can read this mailbox in the tenant the token belongs to (never another
 * tenant). Uses only Mail.Read: no directory permission is requested, so the address must be the
 * user's sign-in name (UPN), which Graph accepts in `/users/{upn}` paths.
 */
export async function probeMailbox(
  appToken: string,
  email: string,
  fetchImpl: typeof fetch = fetch,
): Promise<MailboxProbe> {
  const response = await fetchImpl(
    `${GRAPH}/v1.0/users/${encodeURIComponent(email)}/mailFolders/inbox?$select=id`,
    {
      headers: { Authorization: `Bearer ${appToken}` },
      signal: AbortSignal.timeout(15_000),
    },
  );
  if (response.status === 404) return { ok: false, reason: "not_found" };
  if (response.status === 401 || response.status === 403)
    return { ok: false, reason: "consent_missing" };
  if (!response.ok) throw new Error(`mailbox probe failed: ${String(response.status)}`);
  return { ok: true };
}

/**
 * Google OAuth 2.0 (web server flow + PKCE) for Gmail connections.
 * Docs checked: authorization endpoint (access_type=offline, prompt=consent for a refresh token,
 * include_granted_scopes), token endpoint, ID token claims (sub, email, email_verified, aud, iss).
 */
import { z } from "zod";
import { codeChallenge } from "./pkce.js";

export const GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly";
export const CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.readonly";
export const GOOGLE_SCOPES = ["openid", "email", GMAIL_SCOPE, CALENDAR_SCOPE] as const;

export interface GoogleOAuthConfig {
  clientId: string;
  clientSecret: string;
  redirectUri: string;
}

export function googleAuthorizationUrl(
  config: GoogleOAuthConfig,
  options: { state: string; verifier: string; loginHint?: string },
): string {
  const url = new URL("https://accounts.google.com/o/oauth2/v2/auth");
  url.search = new URLSearchParams({
    client_id: config.clientId,
    redirect_uri: config.redirectUri,
    response_type: "code",
    scope: GOOGLE_SCOPES.join(" "),
    access_type: "offline",
    prompt: "consent",
    include_granted_scopes: "true",
    state: options.state,
    code_challenge: codeChallenge(options.verifier),
    code_challenge_method: "S256",
    ...(options.loginHint ? { login_hint: options.loginHint } : {}),
  }).toString();
  return url.toString();
}

const tokenResponse = z.object({
  refresh_token: z.string().min(1).optional(),
  id_token: z.string().min(1),
  scope: z.string(),
});

const idTokenClaims = z.object({
  iss: z.enum(["https://accounts.google.com", "accounts.google.com"]),
  aud: z.string(),
  sub: z.string().min(1),
  email: z.email(),
  email_verified: z.boolean(),
});

export interface GoogleGrant {
  refreshToken: string;
  sub: string;
  email: string;
  scopes: string[];
}

export class GoogleGrantError extends Error {
  constructor(
    readonly reason: "exchange_failed" | "no_refresh_token" | "missing_scope" | "unverified_email",
  ) {
    super(reason);
    this.name = "GoogleGrantError";
  }
}

export async function exchangeGoogleCode(
  config: GoogleOAuthConfig,
  code: string,
  verifier: string,
  fetchImpl: typeof fetch = fetch,
): Promise<GoogleGrant> {
  const response = await fetchImpl("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      code,
      code_verifier: verifier,
      client_id: config.clientId,
      client_secret: config.clientSecret,
      redirect_uri: config.redirectUri,
    }),
    signal: AbortSignal.timeout(15_000),
  });
  if (!response.ok) throw new GoogleGrantError("exchange_failed");
  const body = tokenResponse.safeParse(await response.json());
  if (!body.success) throw new GoogleGrantError("exchange_failed");
  if (!body.data.refresh_token) throw new GoogleGrantError("no_refresh_token");

  // Received directly from the token endpoint over TLS, so the ID token needs no signature check
  // (OpenID Connect Core 3.1.3.7); issuer and audience are still validated.
  const payload = body.data.id_token.split(".")[1] ?? "";
  const claims = idTokenClaims.safeParse(
    JSON.parse(Buffer.from(payload, "base64url").toString("utf8")),
  );
  if (!claims.success || claims.data.aud !== config.clientId)
    throw new GoogleGrantError("exchange_failed");
  if (!claims.data.email_verified) throw new GoogleGrantError("unverified_email");

  const scopes = body.data.scope.split(" ").filter(Boolean);
  if (!scopes.includes(GMAIL_SCOPE)) throw new GoogleGrantError("missing_scope");
  return {
    refreshToken: body.data.refresh_token,
    sub: claims.data.sub,
    email: claims.data.email.toLowerCase(),
    scopes,
  };
}

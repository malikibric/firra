import type { SupabaseClient } from "@supabase/supabase-js";
import { OAuth2Client } from "google-auth-library";
import type { AuthUser } from "../lib/types.js";

/** Verifies a Supabase access token; null when invalid or expired. */
export type UserTokenVerifier = (token: string) => Promise<AuthUser | null>;

/** Verifies a Google-signed ID token; returns the caller's service account email or null. */
export type IdTokenVerifier = (token: string) => Promise<string | null>;

export function supabaseUserTokenVerifier(client: SupabaseClient): UserTokenVerifier {
  return async (token) => {
    // Verifies the signature locally via JWKS for asymmetric keys, else asks Supabase Auth.
    const { data, error } = await client.auth.getClaims(token);
    if (error || !data?.claims.sub) return null;
    const email = typeof data.claims.email === "string" ? data.claims.email : null;
    return { id: data.claims.sub, email };
  };
}

export function googleIdTokenVerifier(
  audience: string,
  allowedEmails: readonly string[],
): IdTokenVerifier {
  const client = new OAuth2Client();
  const allowed = new Set(allowedEmails.map((email) => email.toLowerCase()));
  return async (token) => {
    try {
      const ticket = await client.verifyIdToken({ idToken: token, audience });
      const payload = ticket.getPayload();
      const email = payload?.email?.toLowerCase();
      if (!email || payload?.email_verified !== true || !allowed.has(email)) return null;
      return email;
    } catch {
      return null;
    }
  };
}

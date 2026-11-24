import { oauthFinishedPage } from "./oauth-return.js";
import { Hono } from "hono";
import { z } from "zod";
import type { UserTokenVerifier } from "../auth/tokens.js";
import type { OrgService } from "../connect/org.js";
import { safeReturnPath, returnUrl } from "../connect/pkce.js";
import type { ApiRepository } from "../lib/repository.js";
import type { AppEnv } from "../lib/types.js";
import { requireUser, requireWorkspaceRole } from "../middleware/auth.js";

interface Deps {
  org: OrgService;
  repo: ApiRepository;
  verifyUser: UserTokenVerifier;
  webUrl: string | undefined;
}

const mailboxes = z.object({
  emails: z
    .array(z.email().max(320))
    .min(1)
    .max(100)
    .transform((emails) => [...new Set(emails.map((email) => email.toLowerCase()))]),
});

/** Organization-wide mailbox access (owner/admin), under /v1/workspaces/:id/org/*. */
export function orgRoutes(deps: Deps): Hono<AppEnv> {
  const app = new Hono<AppEnv>();
  app.use(
    "/workspaces/:workspaceId/org/*",
    requireUser(deps.verifyUser),
    requireWorkspaceRole(deps.repo, ["owner", "admin"]),
  );

  app.post("/workspaces/:workspaceId/org/status", async (c) =>
    c.json(await deps.org.status(c.req.param("workspaceId"), c.get("user").id)),
  );

  app.post("/workspaces/:workspaceId/org/google/mailboxes", async (c) => {
    const body = mailboxes.safeParse(await c.req.json().catch(() => null));
    if (!body.success) return c.json({ error: "invalid_request" }, 400);
    const results = await deps.org.addGoogleMailboxes(
      c.req.param("workspaceId"),
      c.get("user").id,
      body.data.emails,
    );
    return results
      ? c.json({ results })
      : c.json({ error: "google_delegation_not_configured" }, 503);
  });

  app.post("/workspaces/:workspaceId/org/microsoft/consent", async (c) => {
    const body = z
      .object({ return_to: z.string() })
      .safeParse(await c.req.json().catch(() => null));
    const returnTo = safeReturnPath(body.data?.return_to);
    if (!returnTo) return c.json({ error: "invalid_request" }, 400);
    const url = await deps.org.microsoftConsentUrl(
      c.req.param("workspaceId"),
      c.get("user").id,
      returnTo,
    );
    return url ? c.json({ consent_url: url }) : c.json({ error: "no_verified_tenant" }, 409);
  });

  app.post("/workspaces/:workspaceId/org/microsoft/mailboxes", async (c) => {
    const body = mailboxes.safeParse(await c.req.json().catch(() => null));
    if (!body.success) return c.json({ error: "invalid_request" }, 400);
    const results = await deps.org.addMicrosoftMailboxes(
      c.req.param("workspaceId"),
      c.get("user").id,
      body.data.emails,
    );
    return results ? c.json({ results }) : c.json({ error: "no_verified_tenant" }, 409);
  });

  return app;
}

/** GET /oauth/microsoft/admin-callback — where Microsoft returns after admin consent. */
export function orgCallbackRoutes(deps: Deps): Hono<AppEnv> {
  const app = new Hono<AppEnv>();
  app.get("/oauth/microsoft/admin-callback", async (c) => {
    const result = await deps.org.handleAdminConsent({
      state: c.req.query("state"),
      tenant: c.req.query("tenant"),
      adminConsent: c.req.query("admin_consent"),
      error: c.req.query("error"),
    });
    const target = returnUrl(result.returnTo, deps.webUrl);
    if (!target) return oauthFinishedPage(c, result.ok);
    if (result.ok) target.searchParams.set("org_consent", "microsoft");
    else target.searchParams.set("org_error", result.error ?? "failed");
    return c.redirect(target.toString(), 302);
  });
  return app;
}

import { oauthFinishedPage } from "./oauth-return.js";
import { Hono } from "hono";
import { z } from "zod";
import type { UserTokenVerifier } from "../auth/tokens.js";
import { safeReturnPath, returnUrl } from "../connect/pkce.js";
import { ProviderNotConfiguredError, type ConnectService } from "../connect/service.js";
import type { ApiRepository } from "../lib/repository.js";
import type { AppEnv } from "../lib/types.js";
import { requireUser, requireWorkspaceRole } from "../middleware/auth.js";

interface Deps {
  connect: ConnectService;
  repo: ApiRepository;
  verifyUser: UserTokenVerifier;
  webUrl: string | undefined;
}

const oauthProvider = z.enum(["google", "microsoft"]);

function notConfigured(error: unknown) {
  if (error instanceof ProviderNotConfiguredError)
    return { error: `${error.provider}_not_configured` } as const;
  throw error;
}

/** POST /workspaces/:id/connections/:provider/start — owners/admins connect their own mailbox. */
export function memberConnectRoutes(deps: Deps): Hono<AppEnv> {
  const app = new Hono<AppEnv>();
  app.post(
    "/workspaces/:workspaceId/connections/:provider/start",
    requireUser(deps.verifyUser),
    requireWorkspaceRole(deps.repo, ["owner", "admin"]),
    async (c) => {
      const body = z
        .object({ return_to: z.string() })
        .safeParse(await c.req.json().catch(() => null));
      const returnTo = safeReturnPath(body.data?.return_to);
      const provider = oauthProvider.safeParse(c.req.param("provider"));
      if (!returnTo || !provider.success) return c.json({ error: "invalid_request" }, 400);
      try {
        const url = await deps.connect.startForMember(
          c.req.param("workspaceId"),
          c.get("user").id,
          returnTo,
          provider.data,
        );
        return c.json({ authorization_url: url });
      } catch (error) {
        return c.json(notConfigured(error), 503);
      }
    },
  );
  return app;
}

/** Provider callbacks authenticate with one-time OAuth state, without a user session. */
export function publicConnectRoutes(deps: Deps): Hono<AppEnv> {
  const app = new Hono<AppEnv>();

  for (const provider of oauthProvider.options) {
    app.get(`/oauth/${provider}/callback`, async (c) => {
      const params = {
        code: c.req.query("code"),
        state: c.req.query("state"),
        error: c.req.query("error"),
      };
      const result =
        provider === "google"
          ? await deps.connect.handleGoogleCallback(params)
          : await deps.connect.handleMicrosoftCallback(params);
      const target = returnUrl(result.returnTo, deps.webUrl);
      if (!target) return oauthFinishedPage(c, result.ok);
      if (result.ok) target.searchParams.set("connected", provider);
      else target.searchParams.set("connect_error", result.error);
      return c.redirect(target.toString(), 302);
    });
  }

  return app;
}

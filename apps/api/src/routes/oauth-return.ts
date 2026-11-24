import type { Context } from "hono";
import type { AppEnv } from "../lib/types.js";

/** No inferred origin or reflected provider data when the legacy web app is absent. */
export function oauthFinishedPage(c: Context<AppEnv>, ok: boolean) {
  c.header("Cache-Control", "no-store");
  return c.html(
    `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Firra connection</title></head><body><p>${ok ? "The connection finished." : "The connection could not be completed."} You can return to the Firra app.</p></body></html>`,
  );
}

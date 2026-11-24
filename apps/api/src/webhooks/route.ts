import { Hono } from "hono";
import type { AppEnv } from "../lib/types.js";
import type { WebhookProvider } from "./verifier.js";

const MAX_BODY_BYTES = 1_000_000;

/** POST /webhooks/:provider — verifies the signature on the raw body before anything else. */
export function webhookRoutes(providers: readonly WebhookProvider[]): Hono<AppEnv> {
  const registry = new Map(providers.map((provider) => [provider.name, provider]));
  const app = new Hono<AppEnv>();

  app.post("/:provider", async (c) => {
    const provider = registry.get(c.req.param("provider"));
    if (!provider) return c.json({ error: "not_found" }, 404);

    const declaredLength = Number(c.req.header("content-length") ?? "0");
    if (declaredLength > MAX_BODY_BYTES) return c.json({ error: "payload_too_large" }, 413);
    const rawBody = await c.req.text();
    if (Buffer.byteLength(rawBody) > MAX_BODY_BYTES)
      return c.json({ error: "payload_too_large" }, 413);

    const request = { rawBody, headers: c.req.raw.headers };
    if (!(await provider.verifier.verify(request))) {
      c.get("logger").warn("webhook.rejected", { provider: provider.name });
      return c.json({ error: "invalid_signature" }, 401);
    }
    await provider.handle(request);
    return c.json({ ok: true });
  });

  return app;
}

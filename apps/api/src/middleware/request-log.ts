import type { MiddlewareHandler } from "hono";
import { routePath } from "hono/route";
import type { Logger } from "../lib/logger.js";
import type { AppEnv } from "../lib/types.js";

/** Logs method, matched route pattern (never the raw URL), status and duration. */
export function requestLog(logger: Logger): MiddlewareHandler<AppEnv> {
  return async (c, next) => {
    c.set("logger", logger);
    const started = performance.now();
    await next();
    logger.info("http.request", {
      method: c.req.method,
      route: routePath(c, -1),
      status: String(c.res.status),
      duration_ms: Math.round(performance.now() - started),
    });
  };
}

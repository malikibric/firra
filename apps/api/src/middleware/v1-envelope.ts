import { randomUUID } from "node:crypto";
import type { ApiError, ApiErrorCode } from "@firra/contracts";
import type { Context, MiddlewareHandler } from "hono";
import type { ContentfulStatusCode } from "hono/utils/http-status";
import type { AppEnv } from "../lib/types.js";

/** Every `/v1` response carries a request id and is never cached (docs/plans/c-api-v1.md C-D7). */
export function v1Envelope(): MiddlewareHandler<AppEnv> {
  return async (c, next) => {
    const requestId = randomUUID();
    c.set("requestId", requestId);
    c.header("X-Request-Id", requestId);
    c.header("Cache-Control", "no-store");
    await next();
  };
}

/** The `/v1` error body: stable code, short message without row data, request id. */
export function apiFailure(
  c: Context<AppEnv>,
  status: ContentfulStatusCode,
  code: ApiErrorCode,
  message: string,
) {
  const body: ApiError = { error: code, message, request_id: c.get("requestId") };
  return c.json(body, status);
}

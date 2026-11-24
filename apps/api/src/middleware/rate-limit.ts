import type { Context, MiddlewareHandler } from "hono";
import { clientIp } from "../lib/client-ip.js";
import type { AppEnv } from "../lib/types.js";

interface Bucket {
  tokens: number;
  updatedAt: number;
}

/**
 * Per-IP token bucket, in memory per instance. With several Cloud Run instances the effective
 * limit is approximate; a shared limiter (Cloud Armor) replaces this before launch.
 */
export function rateLimit(
  perMinute: number,
  now: () => number = Date.now,
  keyOf: (c: Context<AppEnv>) => string = (c) => clientIp(c.req.header("x-forwarded-for")),
): MiddlewareHandler<AppEnv> {
  const buckets = new Map<string, Bucket>();
  const refillPerMs = perMinute / 60_000;

  return async (c, next) => {
    const ip = keyOf(c);
    const time = now();
    const bucket = buckets.get(ip) ?? { tokens: perMinute, updatedAt: time };
    bucket.tokens = Math.min(perMinute, bucket.tokens + (time - bucket.updatedAt) * refillPerMs);
    bucket.updatedAt = time;

    if (bucket.tokens < 1) {
      buckets.set(ip, bucket);
      c.header("Retry-After", String(Math.ceil((1 - bucket.tokens) / refillPerMs / 1000)));
      return c.json({ error: "rate_limited" }, 429);
    }
    bucket.tokens -= 1;
    buckets.set(ip, bucket);

    if (buckets.size > 10_000) {
      for (const [key, value] of buckets) {
        if (time - value.updatedAt > 60_000) buckets.delete(key);
      }
    }
    await next();
  };
}

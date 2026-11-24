/**
 * Structured JSON logs for Cloud Logging. Same guardrail as the worker: free-text values are
 * redacted unless the key is allowlisted, so bodies, quotes, emails and tokens never reach logs.
 */
const SAFE_STRING_KEYS = new Set([
  "env",
  "error_class",
  "event",
  "method",
  "path",
  "provider",
  "queue",
  "release",
  "route",
  "status",
  "task_type",
]);
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const REDACTED = "[redacted]";

type Severity = "DEBUG" | "INFO" | "WARNING" | "ERROR";
export type LogFields = Record<string, unknown>;

export function sanitizeValue(key: string, value: unknown): unknown {
  if (value === null || value === undefined) return null;
  if (typeof value === "number" || typeof value === "boolean") return value;
  if (value instanceof Date) return value.toISOString();
  if (typeof value === "string") {
    if (SAFE_STRING_KEYS.has(key)) return value.slice(0, 200);
    if (UUID_RE.test(value)) return value;
  }
  return REDACTED;
}

export interface Logger {
  debug(event: string, fields?: LogFields): void;
  info(event: string, fields?: LogFields): void;
  warn(event: string, fields?: LogFields): void;
  error(event: string, fields?: LogFields & { err?: unknown }): void;
}

export function createLogger(
  write: (line: string) => void = (line) => process.stdout.write(`${line}\n`),
): Logger {
  const log = (severity: Severity, event: string, fields: LogFields = {}): void => {
    const entry: Record<string, unknown> = {
      time: new Date().toISOString(),
      severity,
      message: event,
    };
    for (const [key, value] of Object.entries(fields)) {
      if (key === "err") {
        // Error class only; messages can contain content (Sentry receives the full error).
        entry.error_class = value instanceof Error ? value.name : "unknown";
        continue;
      }
      entry[key] = sanitizeValue(key, value);
    }
    write(JSON.stringify(entry));
  };
  return {
    debug: (event, fields) => {
      log("DEBUG", event, fields);
    },
    info: (event, fields) => {
      log("INFO", event, fields);
    },
    warn: (event, fields) => {
      log("WARNING", event, fields);
    },
    error: (event, fields) => {
      log("ERROR", event, fields);
    },
  };
}

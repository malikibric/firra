/**
 * Client address behind Cloud Run's front end. The front end appends the real peer address as
 * the last X-Forwarded-For entry; every earlier entry is client-supplied and must not be trusted.
 */
export function clientIp(forwardedFor: string | undefined | null): string {
  const entries = (forwardedFor ?? "")
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean);
  return entries.at(-1) ?? "unknown";
}

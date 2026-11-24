import { z } from "zod";

export interface PageQuery {
  limit: number;
  cursor: { occurred_at: string; id: string } | null;
}
/** Preserve Postgres microseconds while canonicalizing UTC offsets for lexical ordering. */
export function instant(value: string): string {
  const fraction = /\.(\d+)/.exec(value)?.[1] ?? "";
  return `${new Date(value).toISOString().slice(0, 19)}.${fraction.padEnd(6, "0")}Z`;
}
const timestamp = z.iso.datetime({ offset: true });
const eventId = /^(?:state|signal|outcome|commitment_(?:created|due|closed)):[0-9a-f-]{36}$/;
/** Reject noncanonical encodings and delimiters before any PostgREST filter is constructed. */
export function parsePage(limit = "50", cursor?: string, evidence = false): PageQuery | null {
  if (!/^[0-9]+$/.test(limit) || Number(limit) < 1 || Number(limit) > 100) return null;
  if (cursor === undefined) return { limit: Number(limit), cursor: null };
  if (!/^[A-Za-z0-9_-]+$/.test(cursor) || cursor.length > 256) return null;
  const decoded = Buffer.from(cursor, "base64url").toString("utf8");
  if (Buffer.from(decoded).toString("base64url") !== cursor) return null;
  const parts = decoded.split("|");
  const occurred_at = parts[0] ?? "";
  const id = parts[1] ?? "";
  const uuid = evidence ? id : id.split(":")[1];
  if (
    parts.length !== 2 ||
    !timestamp.safeParse(occurred_at).success ||
    (/\.(\d+)/.exec(occurred_at)?.[1]?.length ?? 0) > 6 ||
    !z.uuid().safeParse(uuid).success ||
    (!evidence && !eventId.test(id))
  )
    return null;
  return { limit: Number(limit), cursor: { occurred_at: instant(occurred_at), id } };
}
export function page<T extends { occurred_at: string; id: string }>(rows: T[], query: PageQuery) {
  const sorted = rows
    .map((row) => ({ ...row, occurred_at: instant(row.occurred_at) }))
    .filter(
      (r) =>
        !query.cursor ||
        r.occurred_at < query.cursor.occurred_at ||
        (r.occurred_at === query.cursor.occurred_at && r.id < query.cursor.id),
    )
    .sort((a, b) => b.occurred_at.localeCompare(a.occurred_at) || b.id.localeCompare(a.id));
  const items = sorted.slice(0, query.limit);
  const last = items.at(-1);
  return {
    items,
    next_cursor:
      sorted.length > query.limit && last
        ? Buffer.from(`${last.occurred_at}|${last.id}`).toString("base64url")
        : null,
  };
}

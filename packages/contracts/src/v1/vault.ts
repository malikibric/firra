import { z } from "zod";

export const vaultRecord = z.object({
  id: z.uuid(),
  workspace_id: z.uuid(),
  provider: z.enum(["gmail", "google_calendar", "outlook", "outlook_calendar", "email_upload"]),
  external_id: z.string(),
  kind: z.enum(["email", "calendar_event"]),
  client_id: z.uuid().nullable(),
  thread_key: z.string().nullable(),
  occurred_at: z.iso.datetime({ offset: true }),
  direction: z.enum(["in", "out", "internal"]),
  subject: z.string().max(300).nullable(),
  deep_link: z.string().nullable(),
  content_hash: z.string().nullable(),
  word_count: z.number().int().nonnegative(),
  body: z.string(),
});
export const vaultRecordsResponse = z.object({
  items: z.array(vaultRecord).max(100),
  next_cursor: z.string().nullable(),
});
export const vaultAckRequest = z.object({ ids: z.array(z.uuid()).min(1).max(200) }).strict();
export const vaultAckResponse = z.object({ acknowledged: z.number().int().min(0).max(200) });
/** How many of the caller's mailboxes were asked to send their mail again. */
export const vaultRefetchResponse = z.object({ requested: z.number().int().min(0).max(50) });
export type VaultRecord = z.infer<typeof vaultRecord>;
export type VaultRecordsResponse = z.infer<typeof vaultRecordsResponse>;

export const vaultKeepRequest = z
  .object({ client_ids: z.array(z.uuid()).min(1).max(200) })
  .strict();
export const vaultKeepResponse = z.object({ keep: z.array(z.uuid()).max(200) });

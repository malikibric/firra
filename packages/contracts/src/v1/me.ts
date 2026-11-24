import { z } from "zod";

export const claimInviteRequest = z.object({ token: z.string().regex(/^[0-9a-f]{64}$/) });
export const claimInviteResponse = z.object({ workspace_id: z.uuid(), contributor_id: z.uuid() });

export const meResponse = z.object({
  user: z.object({ id: z.uuid(), email: z.string().nullable() }),
  /** Workspaces the user owns or administers (full access). */
  memberships: z.array(
    z.object({
      workspace_id: z.uuid(),
      workspace_name: z.string(),
      role: z.enum(["owner", "admin"]),
    }),
  ),
  /** Workspaces where the user only connects their own sources. */
  contributions: z.array(
    z.object({ workspace_id: z.uuid(), workspace_name: z.string(), contributor_id: z.uuid() }),
  ),
});
export type MeResponse = z.infer<typeof meResponse>;

export const contributorConnection = z.object({
  id: z.uuid(),
  provider: z.enum(["gmail", "google_calendar", "outlook", "outlook_calendar", "email_upload"]),
  account_label: z.string(),
  status: z.enum(["active", "error", "revoked"]),
  last_synced_at: z.iso.datetime({ offset: true }).nullable(),
  last_error_class: z.string().nullable(),
});
export const contributorConnectionsResponse = z.object({
  items: z.array(contributorConnection).max(50),
});
export type ContributorConnectionsResponse = z.infer<typeof contributorConnectionsResponse>;

export const contributorConnectStart = z.object({ return_to: z.string().min(1).max(300) });
export const authorizationUrlResponse = z.object({ authorization_url: z.url() });
export type ClaimInviteRequest = z.infer<typeof claimInviteRequest>;

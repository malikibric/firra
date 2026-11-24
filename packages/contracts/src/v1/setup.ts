import { z } from "zod";
import { clientStatus } from "./common.js";

export const setupPolicy = z.enum(["full", "metadata_only", "excluded"]);
export const setupLocale = z.enum(["en", "de", "sv", "fr", "it"]);
const token = z.string().regex(/^[0-9a-f]{64}$/);
const email = z.email().max(254);
const domains = z.array(
  z
    .string()
    .max(253)
    .regex(/^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/),
);
const timestamps = { created_at: z.iso.datetime({ offset: true }) };
const nullableId = z.uuid().nullable();
const count = z.number().int().nonnegative();

export const createWorkspaceRequest = z.object({
  name: z.string().trim().min(1).max(120),
  timezone: z
    .string()
    .min(1)
    .max(100)
    .refine((zone) => zone === "UTC" || Intl.supportedValuesOf("timeZone").includes(zone), {
      message: "Invalid timezone.",
    })
    .default("UTC"),
  default_locale: setupLocale.default("en"),
});
export const acceptInviteRequest = z.object({ token });
export const createClientRequest = z
  .object({
    name: z.string().trim().min(1).max(200),
    policy: setupPolicy.default("full"),
    domains: domains.default([]),
    confirm_exclusion: z.boolean().default(false),
  })
  .refine((input) => input.policy !== "excluded" || input.confirm_exclusion, {
    path: ["confirm_exclusion"],
    message: "Exclusion must be confirmed.",
  })
  .describe("Selecting excluded requires confirm_exclusion: true.");
export const updateClientRequest = z.object({
  name: z.string().trim().min(1).max(200),
  status: clientStatus,
});
export const clientDomainsRequest = z.object({ domains });
export const clientPolicyRequest = z
  .object({
    policy: setupPolicy,
    confirm_exclusion: z.boolean().default(false),
  })
  .refine((input) => input.policy !== "excluded" || input.confirm_exclusion, {
    path: ["confirm_exclusion"],
    message: "Exclusion must be confirmed.",
  })
  .describe("Selecting excluded requires confirm_exclusion: true.");
export const saveServiceRequest = z
  .object({
    name: z.string().trim().min(1).max(120),
    description: z.string().max(1000).default(""),
    keywords: z.array(z.string().trim().min(1).max(1000)).max(20).default([]),
  })
  .refine((input) => input.keywords.join(",").length <= 1000, {
    path: ["keywords"],
    message: "Invalid keywords.",
  })
  .describe("Keywords are normalized and deduplicated; joined input is at most 1000 characters.");
export const inviteAdminRequest = z.object({ email, locale: setupLocale.default("en") });
export const inviteContributorRequest = z.object({
  email,
  name: z.string().trim().max(200).optional(),
  locale: setupLocale.default("en"),
});
export const requestReplayRequest = z
  .object({
    window_start: z.iso.date(),
    window_end: z.iso.date(),
    step_days: z.number().int().min(1).max(31).default(7),
  })
  .refine(
    (input) => {
      const days = (Date.parse(input.window_end) - Date.parse(input.window_start)) / 86400000;
      return days >= 0 && days <= 400;
    },
    { message: "Replay window must be ordered and at most 400 days." },
  );
export const setupIdResponse = z.object({ id: z.uuid() });
export const adminInviteResponse = z.object({
  id: z.uuid(),
  token,
  expires_at: z.iso.datetime({ offset: true }),
  emailed: z.boolean(),
});
export const contributorInviteResponse = z.object({ id: z.uuid(), token });
export const servicesResponse = z.object({
  items: z.array(
    z.object({
      id: z.uuid(),
      name: z.string(),
      description: z.string(),
      keywords: z.array(z.string()),
      ...timestamps,
    }),
  ),
});
export const membersResponse = z.object({
  items: z.array(
    z.object({
      user_id: z.uuid(),
      email,
      role: z.enum(["owner", "admin"]),
      joined_at: z.iso.datetime({ offset: true }),
    }),
  ),
});
export const contributorsResponse = z.object({
  items: z.array(
    z.object({
      id: z.uuid(),
      email,
      name: z.string().nullable(),
      status: z.enum(["invited", "active", "revoked"]),
      ...timestamps,
    }),
  ),
});
export const invitesResponse = z.object({
  items: z.array(
    z.object({ id: z.uuid(), email, expires_at: z.iso.datetime({ offset: true }), ...timestamps }),
  ),
});
export const importProgress = z.object({ total: count, done: count, failed: count });
export const connectionsResponse = z.object({
  items: z.array(
    z.object({
      id: z.uuid(),
      provider: z.enum(["gmail", "google_calendar", "outlook", "outlook_calendar", "email_upload"]),
      mode: z.enum(["user", "org_delegated", "org_app", "upload"]),
      status: z.enum(["active", "error", "revoked"]),
      account_label: z.string(),
      owner_user_id: nullableId,
      owner_contributor_id: nullableId,
      contributor_email: email.nullable(),
      last_synced_at: z.iso.datetime({ offset: true }).nullable(),
      last_error_class: z.string().nullable(),
      imports: importProgress,
      ...timestamps,
    }),
  ),
});
export const setupClientsResponse = z.object({
  items: z.array(
    z.object({
      id: z.uuid(),
      name: z.string(),
      status: clientStatus,
      policy: setupPolicy,
      domains,
      ...timestamps,
    }),
  ),
});
export const clientDomainsResponse = z.object({ domains });
export const auditResponse = z.object({
  items: z.array(
    z.object({
      id: z.uuid(),
      action: z.string(),
      actor_type: z.string(),
      actor_id: nullableId,
      target_type: z.string().nullable(),
      target_id: nullableId,
      ...timestamps,
    }),
  ),
  next_page: z.number().int().min(1).max(200).nullable(),
});
export const setupResponse = z.object({
  workspace: z.object({
    id: z.uuid(),
    name: z.string(),
    timezone: z.string(),
    default_locale: setupLocale,
  }),
  summary: z.object({
    active_connections: count,
    mapped_clients: count,
    full_clients: count,
    metadata_clients: count,
    excluded_clients: count,
    imports_total: count,
    imports_done: count,
    imports_failed: count,
    import_status: z.enum(["needs_attention", "waiting", "ready", "in_progress"]),
  }),
  budget_threshold: z.union([z.literal(50), z.literal(80), z.literal(100)]).nullable(),
});

/** Setup-only metadata, including excluded clients so their policy can be edited. No intelligence. */
export const setupReadRoutes = [
  { path: "/services", operation: "services", response: servicesResponse },
  { path: "/members", operation: "members", response: membersResponse },
  { path: "/contributors", operation: "contributors", response: contributorsResponse },
  { path: "/connections", operation: "connections", response: connectionsResponse },
  { path: "/invites", operation: "invites", response: invitesResponse },
  { path: "/audit", operation: "audit", response: auditResponse },
  { path: "/setup", operation: "setup", response: setupResponse },
  { path: "/setup/clients", operation: "clients", response: setupClientsResponse },
  { path: "/clients/:clientId/domains", operation: "domains", response: clientDomainsResponse },
] as const;
export type SetupRead = (typeof setupReadRoutes)[number]["operation"];
export const setupWriteRoutes = [
  {
    path: "/workspaces",
    method: "POST",
    operation: "createWorkspace",
    request: createWorkspaceRequest,
    response: setupIdResponse,
    signedOnly: true,
  },
  {
    path: "/invites/accept",
    method: "POST",
    operation: "acceptInvite",
    request: acceptInviteRequest,
    response: setupIdResponse,
    signedOnly: true,
  },
  {
    path: "/clients",
    method: "POST",
    operation: "createClient",
    request: createClientRequest,
    response: setupIdResponse,
  },
  {
    path: "/clients/:clientId",
    method: "PATCH",
    operation: "updateClient",
    request: updateClientRequest,
  },
  {
    path: "/clients/:clientId/domains",
    method: "PUT",
    operation: "setDomains",
    request: clientDomainsRequest,
  },
  {
    path: "/clients/:clientId/policy",
    method: "PUT",
    operation: "setPolicy",
    request: clientPolicyRequest,
  },
  {
    path: "/services",
    method: "POST",
    operation: "createService",
    request: saveServiceRequest,
    response: setupIdResponse,
  },
  {
    path: "/services/:serviceId",
    method: "PUT",
    operation: "saveService",
    request: saveServiceRequest,
    response: setupIdResponse,
  },
  { path: "/services/:serviceId", method: "DELETE", operation: "deleteService" },
  {
    path: "/invites",
    method: "POST",
    operation: "inviteAdmin",
    request: inviteAdminRequest,
    response: adminInviteResponse,
  },
  { path: "/invites/:inviteId", method: "DELETE", operation: "revokeInvite" },
  { path: "/members/:userId", method: "DELETE", operation: "removeMember" },
  {
    path: "/contributors",
    method: "POST",
    operation: "inviteContributor",
    request: inviteContributorRequest,
    response: contributorInviteResponse,
  },
  { path: "/contributors/:contributorId", method: "DELETE", operation: "revokeContributor" },
  { path: "/connections/:connectionId", method: "DELETE", operation: "disconnect" },
  {
    path: "/replay/runs",
    method: "POST",
    operation: "requestReplay",
    request: requestReplayRequest,
    response: setupIdResponse,
  },
] as const;
export type SetupWrite = (typeof setupWriteRoutes)[number]["operation"];

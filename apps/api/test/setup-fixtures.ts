import type { SetupRead, SetupWrite } from "@firra/contracts";
export const WS = "11111111-1111-4111-8111-111111111111";
export const WS_B = "99999999-9999-4999-8999-999999999999";
export const ID = "22222222-2222-4222-8222-222222222222";
export const ID_B = "88888888-8888-4888-8888-888888888888";
export const AT = "2026-10-02T00:00:00Z";
export const TOKEN = "a".repeat(64);
export const inputs: Partial<Record<SetupWrite, unknown>> = {
  createWorkspace: { name: "Synthetic workspace", timezone: "UTC", default_locale: "en" },
  acceptInvite: { token: TOKEN },
  createClient: { name: "Synthetic client", policy: "full", domains: ["example.com"] },
  updateClient: { name: "Renamed", status: "churned" },
  setDomains: { domains: ["example.org"] },
  setPolicy: { policy: "metadata_only" },
  createService: { name: "Consulting", description: "Synthetic description", keywords: ["crm"] },
  saveService: { name: "Consulting", keywords: ["crm"] },
  inviteAdmin: { email: "admin@example.com" },
  inviteContributor: { email: "contributor@example.com", name: "Synthetic" },
  requestReplay: { window_start: "2026-01-01", window_end: "2026-02-01", step_days: 7 },
};
export const reads: Record<SetupRead, unknown> = {
  services: {
    items: [{ id: ID, name: "Service", description: "Synthetic", keywords: [], created_at: AT }],
  },
  members: { items: [{ user_id: ID, email: "admin@example.com", role: "admin", joined_at: AT }] },
  contributors: {
    items: [
      {
        id: ID,
        email: "contributor@example.com",
        name: "Synthetic",
        status: "invited",
        created_at: AT,
      },
    ],
  },
  invites: { items: [{ id: ID, email: "admin@example.com", expires_at: AT, created_at: AT }] },
  connections: {
    items: [
      {
        id: ID,
        provider: "gmail",
        mode: "user",
        status: "active",
        account_label: "synthetic@example.com",
        owner_user_id: ID,
        owner_contributor_id: null,
        contributor_email: null,
        last_synced_at: null,
        last_error_class: null,
        imports: { total: 2, done: 1, failed: 0 },
        created_at: AT,
      },
    ],
  },
  clients: {
    items: [
      {
        id: ID,
        name: "Excluded synthetic client",
        status: "active",
        policy: "excluded",
        domains: ["example.com"],
        created_at: AT,
      },
    ],
  },
  domains: { domains: ["example.com"] },
  audit: {
    items: [
      {
        id: ID,
        action: "client.created",
        actor_type: "user",
        actor_id: ID,
        target_type: "client",
        target_id: ID,
        created_at: AT,
      },
    ],
    next_page: null,
  },
  setup: {
    workspace: { id: WS, name: "Synthetic", timezone: "UTC", default_locale: "en" },
    summary: {
      active_connections: 1,
      mapped_clients: 1,
      full_clients: 1,
      metadata_clients: 0,
      excluded_clients: 1,
      imports_total: 2,
      imports_done: 1,
      imports_failed: 0,
      import_status: "in_progress",
    },
    budget_threshold: 80,
  },
};
export function writeResult(operation: SetupWrite): unknown {
  if (operation === "inviteAdmin")
    return {
      id: ID,
      token: TOKEN,
      expires_at: AT,
      emailed: false,
    };
  if (operation === "inviteContributor") return { id: ID, token: TOKEN };
  return { id: ID };
}

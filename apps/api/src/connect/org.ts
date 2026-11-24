/**
 * Organization-wide mailbox access, set up by an owner/admin for selected people.
 *
 * - Google Workspace domain-wide delegation: the customer's super admin authorizes the worker's
 *   service account (client id below) for the Gmail/Calendar read-only scopes in the Google Admin
 *   console; the owner then lists the mailboxes. The worker signs delegation assertions keylessly.
 * - Microsoft 365 application permissions: a tenant admin grants consent. The tenant is never
 *   taken from the consent redirect alone: it must equal the tenant of the requesting member's
 *   own Outlook connection (verified from the ID token at connect time), so nobody can attach
 *   another organization's tenant to their workspace. Mailboxes are probed in that tenant only.
 */
import type { Keyring } from "../crypto/keyring.js";
import type { Logger } from "../lib/logger.js";
import type { TaskService } from "../tasks/enqueue.js";
import { startConnectionSync } from "./backfill.js";
import { CALENDAR_SCOPE, GMAIL_SCOPE } from "./google.js";
import {
  microsoftAdminConsentUrl,
  microsoftAppToken,
  type MicrosoftOAuthConfig,
  probeMailbox,
} from "./microsoft.js";
import { randomToken, sha256Hex } from "./pkce.js";
import type { ConnectionProvider, ConnectStore } from "./store.js";

export const GOOGLE_DELEGATION_SCOPES = [GMAIL_SCOPE, CALENDAR_SCOPE] as const;
const ADMIN_STATE_PREFIX = "admin-consent:";

interface Deps {
  store: ConnectStore;
  keyring: Keyring;
  tasks: TaskService;
  logger: Logger;
  /** Numeric OAuth client id of the worker service account (null: delegation not deployed). */
  googleDelegationClientId: string | null;
  microsoft: MicrosoftOAuthConfig | null;
  microsoftAdminRedirectUri: string;
  backfillMonths?: number;
  fetchImpl?: typeof fetch;
  now?: () => Date;
}

export interface AddResult {
  email: string;
  status: "added" | "not_found" | "consent_missing";
}

export class OrgService {
  constructor(private readonly deps: Deps) {}

  private async verifiedTenant(workspaceId: string, userId: string): Promise<string | null> {
    const own = await this.deps.store.memberOutlookConnection(workspaceId, userId);
    if (!own) return null;
    const stored = JSON.parse(
      await this.deps.keyring.decryptToken(workspaceId, own.connectionId, own.ciphertext),
    ) as { tenant_id?: unknown };
    return typeof stored.tenant_id === "string" ? stored.tenant_id : null;
  }

  async status(workspaceId: string, userId: string) {
    return {
      google: this.deps.googleDelegationClientId
        ? { client_id: this.deps.googleDelegationClientId, scopes: [...GOOGLE_DELEGATION_SCOPES] }
        : null,
      microsoft: this.deps.microsoft
        ? { tenant_id: await this.verifiedTenant(workspaceId, userId) }
        : null,
    };
  }

  private async connect(
    workspaceId: string,
    userId: string,
    mode: "org_delegated" | "org_app",
    providers: ConnectionProvider[],
    email: string,
    externalAccountId: string,
    tokenJson: string | null,
  ): Promise<void> {
    for (const provider of providers) {
      const id = await this.deps.store.upsertConnection({
        workspaceId,
        provider,
        mode,
        ownerUserId: null,
        ownerContributorId: null,
        accountLabel: email,
        externalAccountId,
        scopes: [],
      });
      if (tokenJson) {
        const ciphertext = await this.deps.keyring.encryptToken(workspaceId, id, tokenJson);
        await this.deps.store.saveToken(workspaceId, id, ciphertext);
      }
      await this.deps.store.audit({
        workspaceId,
        actorUserId: userId,
        action: "connection.org_added",
        targetType: "connection",
        targetId: id,
        metadata: { provider, mode },
      });
      await this.startSync(workspaceId, id);
    }
  }

  /** Adds Google Workspace mailboxes through domain-wide delegation. */
  async addGoogleMailboxes(
    workspaceId: string,
    userId: string,
    emails: string[],
  ): Promise<AddResult[] | null> {
    if (!this.deps.googleDelegationClientId) return null;
    const results: AddResult[] = [];
    for (const email of emails) {
      await this.connect(
        workspaceId,
        userId,
        "org_delegated",
        ["gmail", "google_calendar"],
        email,
        email,
        null,
      );
      results.push({ email, status: "added" });
    }
    return results;
  }

  /** Admin consent URL for the member's verified tenant; null when there is none. */
  async microsoftConsentUrl(
    workspaceId: string,
    userId: string,
    returnTo: string,
  ): Promise<string | null> {
    const config = this.deps.microsoft;
    const tenantId = await this.verifiedTenant(workspaceId, userId);
    if (!config || !tenantId) return null;
    const state = randomToken();
    await this.deps.store.savePending(sha256Hex(state), {
      workspaceId,
      provider: "microsoft",
      ownerUserId: userId,
      ownerContributorId: null,
      // Marks an admin-consent state and pins the expected tenant (no PKCE in this flow).
      codeVerifier: `${ADMIN_STATE_PREFIX}${tenantId}:${randomToken(12)}`,
      returnTo,
    });
    return microsoftAdminConsentUrl(config, {
      tenantId,
      state,
      redirectUri: this.deps.microsoftAdminRedirectUri,
    });
  }

  async handleAdminConsent(params: {
    state?: string;
    tenant?: string;
    adminConsent?: string;
    error?: string;
  }): Promise<{ ok: boolean; returnTo: string | null; error?: string }> {
    if (!params.state) return { ok: false, returnTo: null, error: "invalid_state" };
    const pending = await this.deps.store.consumePending(sha256Hex(params.state));
    if (pending?.provider !== "microsoft" || !pending.codeVerifier.startsWith(ADMIN_STATE_PREFIX))
      return { ok: false, returnTo: null, error: "invalid_state" };
    const expected = pending.codeVerifier.slice(ADMIN_STATE_PREFIX.length).split(":")[0];
    if (params.error || params.adminConsent?.toLowerCase() !== "true")
      return { ok: false, returnTo: pending.returnTo, error: "access_denied" };
    if (params.tenant !== expected)
      return { ok: false, returnTo: pending.returnTo, error: "tenant_mismatch" };
    if (pending.ownerUserId) {
      await this.deps.store.audit({
        workspaceId: pending.workspaceId,
        actorUserId: pending.ownerUserId,
        action: "connection.org_consent_granted",
        metadata: { provider: "microsoft" },
      });
    }
    return { ok: true, returnTo: pending.returnTo };
  }

  /** Adds Microsoft 365 mailboxes of the member's verified tenant (application permissions). */
  async addMicrosoftMailboxes(
    workspaceId: string,
    userId: string,
    emails: string[],
  ): Promise<AddResult[] | null> {
    const config = this.deps.microsoft;
    const tenantId = await this.verifiedTenant(workspaceId, userId);
    if (!config || !tenantId) return null;
    const token = await microsoftAppToken(config, tenantId, this.deps.fetchImpl);
    if (!token) return emails.map((email) => ({ email, status: "consent_missing" }));
    const results: AddResult[] = [];
    for (const email of emails) {
      const probe = await probeMailbox(token, email, this.deps.fetchImpl);
      if (!probe.ok) {
        results.push({ email, status: probe.reason });
        continue;
      }
      await this.connect(
        workspaceId,
        userId,
        "org_app",
        ["outlook", "outlook_calendar"],
        email,
        email,
        JSON.stringify({ tenant_id: tenantId }),
      );
      results.push({ email, status: "added" });
    }
    this.deps.logger.info("connect.org_microsoft_added", {
      workspace_id: workspaceId,
      count: results.filter((r) => r.status === "added").length,
    });
    return results;
  }

  private startSync(workspaceId: string, connectionId: string): Promise<void> {
    return startConnectionSync(
      this.deps.tasks,
      workspaceId,
      connectionId,
      (this.deps.now ?? (() => new Date()))(),
      this.deps.backfillMonths ?? 12,
    );
  }
}

import type { Keyring } from "../crypto/keyring.js";
import type { Logger } from "../lib/logger.js";
import type { TaskService } from "../tasks/enqueue.js";
import { startConnectionSync } from "./backfill.js";
import {
  CALENDAR_SCOPE,
  exchangeGoogleCode,
  googleAuthorizationUrl,
  GoogleGrantError,
  type GoogleOAuthConfig,
} from "./google.js";
import {
  CALENDARS_SCOPE,
  exchangeMicrosoftCode,
  microsoftAuthorizationUrl,
  MicrosoftGrantError,
  type MicrosoftOAuthConfig,
} from "./microsoft.js";
import { randomToken, sha256Hex } from "./pkce.js";
import type { ConnectionProvider, ConnectStore, OAuthProvider } from "./store.js";

export interface CallbackParams {
  code?: string;
  state?: string;
  error?: string;
}

export type CallbackResult =
  | { ok: true; returnTo: string; connectionId: string }
  | { ok: false; returnTo: string | null; error: string };

interface Deps {
  store: ConnectStore;
  keyring: Keyring;
  tasks: TaskService;
  google: GoogleOAuthConfig | null;
  microsoft?: MicrosoftOAuthConfig | null;
  logger: Logger;
  backfillMonths?: number;
  fetchImpl?: typeof fetch;
  now?: () => Date;
}

export class ProviderNotConfiguredError extends Error {
  constructor(readonly provider: OAuthProvider) {
    super(`${provider} oauth is not configured`);
    this.name = "ProviderNotConfiguredError";
  }
}

/** What a provider's code exchange yields, in the shape the connection rows need. */
interface Grant {
  accountLabel: string;
  externalAccountId: string;
  scopes: string[];
  /** Plaintext stored encrypted per connection; the worker reads the same JSON. */
  tokenJson: string;
  providers: ConnectionProvider[];
}

/** Connect flows: start (state + PKCE), provider callback, token storage, first sync. */
export class ConnectService {
  constructor(private readonly deps: Deps) {}

  private google(): GoogleOAuthConfig {
    if (!this.deps.google) throw new ProviderNotConfiguredError("google");
    return this.deps.google;
  }

  private microsoft(): MicrosoftOAuthConfig {
    if (!this.deps.microsoft) throw new ProviderNotConfiguredError("microsoft");
    return this.deps.microsoft;
  }

  private authorizationUrl(
    provider: OAuthProvider,
    options: { state: string; verifier: string; loginHint?: string },
  ): string {
    return provider === "google"
      ? googleAuthorizationUrl(this.google(), options)
      : microsoftAuthorizationUrl(this.microsoft(), options);
  }

  private async start(
    provider: OAuthProvider,
    workspaceId: string,
    owner: { userId: string } | { contributorId: string },
    returnTo: string,
    loginHint?: string,
  ): Promise<string> {
    const state = randomToken();
    const verifier = randomToken(48);
    // Fails before storing anything when the provider is not configured.
    const url = this.authorizationUrl(provider, {
      state,
      verifier,
      ...(loginHint ? { loginHint } : {}),
    });
    await this.deps.store.savePending(sha256Hex(state), {
      workspaceId,
      provider,
      ownerUserId: "userId" in owner ? owner.userId : null,
      ownerContributorId: "contributorId" in owner ? owner.contributorId : null,
      codeVerifier: verifier,
      returnTo,
    });
    return url;
  }

  startForMember(
    workspaceId: string,
    userId: string,
    returnTo: string,
    provider: OAuthProvider = "google",
  ): Promise<string> {
    return this.start(provider, workspaceId, { userId }, returnTo);
  }

  /** A signed-in contributor connects their own source; no link token involved. */
  async startForContributorUser(
    workspaceId: string,
    userId: string,
    returnTo: string,
    provider: OAuthProvider = "google",
  ): Promise<string | null> {
    const link = await this.deps.store.contributorByUser(workspaceId, userId);
    if (!link) return null;
    return this.start(
      provider,
      link.workspaceId,
      { contributorId: link.contributorId },
      returnTo,
      link.email,
    );
  }

  handleGoogleCallback(params: CallbackParams): Promise<CallbackResult> {
    return this.handleCallback("google", params);
  }

  handleMicrosoftCallback(params: CallbackParams): Promise<CallbackResult> {
    return this.handleCallback("microsoft", params);
  }

  private async exchange(provider: OAuthProvider, code: string, verifier: string): Promise<Grant> {
    if (provider === "google") {
      const grant = await exchangeGoogleCode(this.google(), code, verifier, this.deps.fetchImpl);
      // Gmail is required; Calendar is connected too when the user granted it.
      return {
        accountLabel: grant.email,
        externalAccountId: grant.sub,
        scopes: grant.scopes,
        tokenJson: JSON.stringify({ refresh_token: grant.refreshToken }),
        providers: grant.scopes.includes(CALENDAR_SCOPE) ? ["gmail", "google_calendar"] : ["gmail"],
      };
    }
    const grant = await exchangeMicrosoftCode(
      this.microsoft(),
      code,
      verifier,
      this.deps.fetchImpl,
    );
    return {
      accountLabel: grant.email,
      externalAccountId: grant.objectId,
      scopes: grant.scopes,
      tokenJson: JSON.stringify({ refresh_token: grant.refreshToken, tenant_id: grant.tenantId }),
      providers: grant.scopes.includes(CALENDARS_SCOPE)
        ? ["outlook", "outlook_calendar"]
        : ["outlook"],
    };
  }

  private async handleCallback(
    provider: OAuthProvider,
    params: CallbackParams,
  ): Promise<CallbackResult> {
    if (!params.state) return { ok: false, returnTo: null, error: "invalid_state" };
    const pending = await this.deps.store.consumePending(sha256Hex(params.state));
    // A state is only valid at the callback of the provider it was issued for.
    if (pending?.provider !== provider)
      return { ok: false, returnTo: null, error: "invalid_state" };
    if (params.error || !params.code)
      return { ok: false, returnTo: pending.returnTo, error: "access_denied" };

    let grant: Grant;
    try {
      grant = await this.exchange(provider, params.code, pending.codeVerifier);
    } catch (error) {
      const reason =
        error instanceof GoogleGrantError || error instanceof MicrosoftGrantError
          ? error.reason
          : "exchange_failed";
      this.deps.logger.warn(`connect.${provider}_failed`, {
        error_class: reason,
        workspace_id: pending.workspaceId,
      });
      return { ok: false, returnTo: pending.returnTo, error: reason };
    }

    let connectionId = "";
    for (const connectionProvider of grant.providers) {
      const id = await this.deps.store.upsertConnection({
        workspaceId: pending.workspaceId,
        provider: connectionProvider,
        ownerUserId: pending.ownerUserId,
        ownerContributorId: pending.ownerContributorId,
        accountLabel: grant.accountLabel,
        externalAccountId: grant.externalAccountId,
        scopes: grant.scopes,
      });
      // Each connection gets its own ciphertext (the AAD binds it to that row).
      const ciphertext = await this.deps.keyring.encryptToken(
        pending.workspaceId,
        id,
        grant.tokenJson,
      );
      await this.deps.store.saveToken(pending.workspaceId, id, ciphertext);
      await this.startSync(pending.workspaceId, id);
      connectionId ||= id;
    }
    if (pending.ownerContributorId) {
      await this.deps.store.activateContributor(pending.workspaceId, pending.ownerContributorId);
    }
    this.deps.logger.info(`connect.${provider}_connected`, {
      workspace_id: pending.workspaceId,
      connection_id: connectionId,
      count: grant.providers.length,
    });
    return { ok: true, returnTo: pending.returnTo, connectionId };
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

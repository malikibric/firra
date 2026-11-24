export interface WebhookRequest {
  rawBody: string;
  headers: Headers;
}

export interface WebhookVerifier {
  /** True only if the request provably comes from the provider (signature + freshness). */
  verify(request: WebhookRequest): boolean | Promise<boolean>;
}

export interface WebhookProvider {
  name: string;
  verifier: WebhookVerifier;
  /** Runs after verification. Should enqueue work and return quickly. */
  handle(request: WebhookRequest): Promise<void>;
}

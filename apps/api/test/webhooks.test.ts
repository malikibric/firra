import { createHmac } from "node:crypto";
import { describe, expect, it, vi } from "vitest";
import { hmacSha256Verifier } from "../src/webhooks/hmac.js";
import type { WebhookProvider } from "../src/webhooks/verifier.js";
import { testApp } from "./helpers.js";

const SECRET = "whsec_test_secret";
const NOW = 1_800_000_000;

// Slack-style scheme: "v0=" + hex(HMAC(secret, "v0:{ts}:{body}")) with a 5-minute window.
const verifier = hmacSha256Verifier({
  secret: SECRET,
  signatureHeader: "x-signature",
  signaturePrefix: "v0=",
  timestampHeader: "x-timestamp",
  toleranceSeconds: 300,
  encoding: "hex",
  signedContent: (body, ts) => `v0:${ts ?? ""}:${body}`,
  now: () => NOW,
});

function sign(body: string, ts: number, secret = SECRET): Record<string, string> {
  const signature = createHmac("sha256", secret)
    .update(`v0:${String(ts)}:${body}`)
    .digest("hex");
  return { "x-signature": `v0=${signature}`, "x-timestamp": String(ts) };
}

function request(body: string, headers: Record<string, string>) {
  return { rawBody: body, headers: new Headers(headers) };
}

describe("hmacSha256Verifier", () => {
  const body = '{"event":"x"}';

  it("accepts a valid, fresh signature", () => {
    expect(verifier.verify(request(body, sign(body, NOW)))).toBe(true);
  });

  it.each([
    ["wrong secret", () => sign(body, NOW, "other")],
    ["stale timestamp", () => sign(body, NOW - 301)],
    ["future timestamp", () => sign(body, NOW + 301)],
    ["missing signature", () => ({ "x-timestamp": String(NOW) })],
    [
      "missing prefix",
      () => ({ ...sign(body, NOW), "x-signature": sign(body, NOW)["x-signature"]?.slice(3) ?? "" }),
    ],
    ["truncated signature", () => ({ ...sign(body, NOW), "x-signature": "v0=abcd" })],
  ])("rejects %s", (_name, headers) => {
    expect(verifier.verify(request(body, headers()))).toBe(false);
  });

  it("rejects a tampered body", () => {
    expect(verifier.verify(request('{"event":"y"}', sign(body, NOW)))).toBe(false);
  });

  it("supports body-only base64 schemes without timestamps", () => {
    const simple = hmacSha256Verifier({
      secret: SECRET,
      signatureHeader: "x-sig",
      encoding: "base64",
      signedContent: (raw) => raw,
    });
    const sig = createHmac("sha256", SECRET).update(body).digest("base64");
    expect(simple.verify(request(body, { "x-sig": sig }))).toBe(true);
    expect(simple.verify(request(`${body} `, { "x-sig": sig }))).toBe(false);
  });
});

describe("POST /webhooks/:provider", () => {
  function appWithProvider() {
    const handle = vi.fn().mockResolvedValue(undefined);
    const provider: WebhookProvider = { name: "fake", verifier, handle };
    return { ...testApp({ webhooks: [provider] }), handle };
  }

  it("404s for unknown providers", async () => {
    const { app } = appWithProvider();
    expect((await app.request("/webhooks/unknown", { method: "POST", body: "{}" })).status).toBe(
      404,
    );
  });

  it("401s on a bad signature without running the handler", async () => {
    const { app, handle } = appWithProvider();
    const response = await app.request("/webhooks/fake", {
      method: "POST",
      body: "{}",
      headers: sign("other", NOW),
    });
    expect(response.status).toBe(401);
    expect(handle).not.toHaveBeenCalled();
  });

  it("runs the handler on the exact raw body after verification", async () => {
    const { app, handle } = appWithProvider();
    const body = '{"a": 1,   "b": [2]}';
    const response = await app.request("/webhooks/fake", {
      method: "POST",
      body,
      headers: sign(body, NOW),
    });
    expect(response.status).toBe(200);
    expect(handle).toHaveBeenCalledWith(expect.objectContaining({ rawBody: body }));
  });

  it("413s on oversized payloads", async () => {
    const { app } = appWithProvider();
    const body = "x".repeat(1_000_001);
    expect((await app.request("/webhooks/fake", { method: "POST", body })).status).toBe(413);
  });
});

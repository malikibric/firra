export interface InviteEmail {
  email: string;
  workspace: string;
  token: string;
}
export type InviteEmailSender = (invite: InviteEmail) => Promise<boolean>;

/** English-only, best-effort delivery; the invite code remains available in the API response. */
export function inviteEmailSender(
  apiKey: string | undefined,
  from: string | undefined,
  send: typeof fetch = fetch,
): InviteEmailSender {
  return async (invite) => {
    if (!apiKey || !from) return false;
    // A workspace name is free text: line breaks and control characters never reach the mail headers.
    const workspace = invite.workspace.replace(/[\r\n\p{Cc}]+/gu, " ").trim() || "Firra";
    const response = await send("https://api.resend.com/emails", {
      method: "POST",
      headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
      body: JSON.stringify({
        from,
        to: [invite.email],
        subject: `You're invited to join ${workspace} on Firra`,
        text: `You've been invited to join ${workspace} on Firra.\n\nOpen Firra for Mac, sign in, choose "Join", and enter this code: ${invite.token}\n\nThe code works once. If you weren't expecting this, you can ignore this email.`,
      }),
      signal: AbortSignal.timeout(10_000),
    }).catch(() => null);
    return response?.ok ?? false;
  };
}

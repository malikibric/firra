import { z } from "zod";

export const createMcpTokenRequest = z.object({
  name: z.string().trim().min(1).max(80),
  days: z.number().int().min(1).max(365).default(90),
});
export type CreateMcpTokenRequest = z.infer<typeof createMcpTokenRequest>;

/** The secret is shown once, in this response only. */
export const mcpTokenCreated = z.object({
  id: z.uuid(),
  token: z.string().regex(/^frm_[0-9a-f]{64}$/),
  token_prefix: z.string().regex(/^frm_[0-9a-f]{8}$/),
  expires_at: z.iso.datetime({ offset: true }),
});
export type McpTokenCreated = z.infer<typeof mcpTokenCreated>;

export const mcpTokenItem = z.object({
  id: z.uuid(),
  user_id: z.uuid(),
  name: z.string(),
  token_prefix: z.string().regex(/^frm_[0-9a-f]{8}$/),
  scopes: z.array(z.string()),
  expires_at: z.iso.datetime({ offset: true }),
  last_used_at: z.iso.datetime({ offset: true }).nullable(),
  revoked_at: z.iso.datetime({ offset: true }).nullable(),
  created_at: z.iso.datetime({ offset: true }),
});
export type McpTokenItem = z.infer<typeof mcpTokenItem>;

export const mcpTokensResponse = z.object({ items: z.array(mcpTokenItem).max(200) });
export type McpTokensResponse = z.infer<typeof mcpTokensResponse>;

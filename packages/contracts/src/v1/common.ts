import { z } from "zod";

/** Stable error codes of the Firra API (docs/plans/c-api-v1.md, C-D7). */
export const apiErrorCode = z.enum([
  "unauthorized",
  "forbidden",
  "not_found",
  "invalid_request",
  "rate_limited",
  "reads_not_configured",
  "internal_error",
]);
export type ApiErrorCode = z.infer<typeof apiErrorCode>;

export const apiError = z.object({
  error: apiErrorCode,
  message: z.string().max(200),
  request_id: z.string().max(64),
});
export type ApiError = z.infer<typeof apiError>;

export const riskState = z.enum(["stable", "watch", "elevated", "high"]);
export const expansionState = z.enum(["none", "hypothesis", "strong"]);
export const evidenceStrength = z.enum(["weak", "moderate", "strong"]);
export const clientStatus = z.enum(["active", "churned", "prospect"]);
export const clientPolicy = z.enum(["full", "metadata_only"]);

export const isoDate = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);

import { z } from "zod";
import { clientDetail, clientAssessment } from "./clients.js";
import { evidenceItem } from "./timeline.js";
import { replayRunsResponse, replayClientResponse } from "./replay.js";

// Reuse the read-model contracts; only the MCP evidence field name differs.
export const mcpClientChange = z.object({
  generated_at: z.iso.datetime(),
  client_id: z.uuid(),
  state_history: clientDetail.shape.state_history,
  assessment: clientAssessment.nullable(),
});
export const mcpCommitments = z.object({
  generated_at: z.iso.datetime(),
  client_id: z.uuid(),
  items: clientDetail.shape.commitments,
});
export const mcpStakeholders = z.object({
  generated_at: z.iso.datetime(),
  client_id: z.uuid(),
  items: clientDetail.shape.stakeholders,
});
export const mcpUnknowns = z.object({
  generated_at: z.iso.datetime(),
  client_id: z.uuid(),
  items: z.array(clientDetail.shape.unknowns.unwrap().element),
});
export const mcpEvidenceItem = evidenceItem.omit({ quote: true }).extend({
  untrusted_quote: evidenceItem.shape.quote,
});
export const mcpEvidence = z.object({
  items: z.array(mcpEvidenceItem),
  next_cursor: z.string().nullable(),
});
export const mcpReplay = z
  .object({
    runs: replayRunsResponse.optional(),
    client: replayClientResponse.optional(),
  })
  .refine((r) => (r.runs !== undefined) !== (r.client !== undefined));

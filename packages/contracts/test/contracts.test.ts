import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import type { z } from "zod";
import {
  aiExtractionOutput,
  aiSettings,
  calendarEventRecord,
  chatMessage,
  diagnosticsPingPayload,
  engineStatus,
  handshakeMessage,
  ingestUploadPayload,
  jobStatus,
  message,
  taskEnvelope,
} from "../src/index.js";

// Shared with python/tests/test_contracts.py so both generators must agree.
const cases = JSON.parse(
  readFileSync(new URL("../fixtures/cases.json", import.meta.url), "utf8"),
) as Record<string, { valid: unknown[]; invalid: unknown[] }>;

const schemas: Record<string, z.ZodType> = {
  TaskEnvelope: taskEnvelope,
  DiagnosticsPingPayload: diagnosticsPingPayload,
  JobStatus: jobStatus,
  Message: message,
  CalendarEventRecord: calendarEventRecord,
  IngestUploadPayload: ingestUploadPayload,
  HandshakeMessage: handshakeMessage,
  AiExtractionOutput: aiExtractionOutput,
  AiSettings: aiSettings,
  EngineStatus: engineStatus,
  ChatMessage: chatMessage,
};

describe.each(Object.entries(schemas))("%s", (name, schema) => {
  const { valid, invalid } = cases[name] ?? { valid: [], invalid: [] };

  it.each(valid.map((v) => [JSON.stringify(v), v]))("accepts %s", (_label, value) => {
    expect(schema.safeParse(value).success).toBe(true);
  });

  it.each(invalid.map((v) => [JSON.stringify(v), v]))("rejects %s", (_label, value) => {
    expect(schema.safeParse(value).success).toBe(false);
  });
});

import { z } from "zod";
import { replayRunsResponse, replayClientResponse } from "./replay.js";
import { attentionResponse } from "./attention.js";
import { meetingsResponse } from "./meetings.js";
import { changesResponse } from "./changes.js";
import { clientsResponse, clientDetailResponse } from "./clients.js";
import { timelineResponse, evidenceResponse, evidenceItem } from "./timeline.js";
import {
  cardImportance,
  decisionKind,
  decisionsResponse,
  feedbackRequest,
  outcomeRequest,
  mutationResponse,
} from "./decisions.js";
import {
  vaultKeepRequest,
  vaultKeepResponse,
  vaultRecordsResponse,
  vaultAckRequest,
  vaultAckResponse,
  vaultRefetchResponse,
} from "./vault.js";
import { setupReadRoutes, setupWriteRoutes } from "./setup.js";
import { routineKind, routineEnabledRequest, routinesResponse } from "./routines.js";
import { apiError } from "./common.js";

function schema(type: z.ZodType, io: "input" | "output" = "output"): Record<string, unknown> {
  const json: Record<string, unknown> = { ...z.toJSONSchema(type, { io }) };
  delete json.$schema;
  return json;
}

const errorResponse = (description: string) => ({
  description,
  content: { "application/json": { schema: { $ref: "#/components/schemas/ApiError" } } },
});

const workspaceId = {
  name: "workspaceId",
  in: "path",
  required: true,
  schema: { type: "string", format: "uuid" },
};

function clientResponses(model: string) {
  return {
    "200": {
      description: "Caller-visible client data",
      content: { "application/json": { schema: { $ref: `#/components/schemas/${model}` } } },
    },
    "401": errorResponse("Missing or invalid token"),
    "403": errorResponse("Not an owner or admin of the workspace"),
    "422": errorResponse("Invalid path id or query"),
    "429": errorResponse("Rate limited"),
    "503": errorResponse("Reads are not configured on this deployment"),
    "500": errorResponse("Read or audit failed"),
  };
}

function vaultResponses(model: string) {
  const responses = clientResponses(model);
  const userResponses = Object.fromEntries(
    Object.entries(responses).filter(([status]) => status !== "403"),
  );
  return {
    ...userResponses,
    "200": {
      description: "Caller's own vault data; audited and never cached",
      content: { "application/json": { schema: { $ref: `#/components/schemas/${model}` } } },
    },
  };
}

/** OpenAPI 3.1 document of `/v1`, generated from the zod schemas (docs/plans/c-api-v1.md, C-D6). */
export function openApiDocument(version: string): Record<string, unknown> {
  const document = {
    openapi: "3.1.0",
    info: { title: "Firra API", version },
    servers: [{ url: "/v1" }],
    security: [{ bearer: [] }],
    paths: {
      "/vault/keep": {
        post: {
          operationId: "keepVaultClients",
          summary: "Requested clients whose mail the caller may keep on this Mac",
          requestBody: {
            required: true,
            content: {
              "application/json": { schema: { $ref: "#/components/schemas/VaultKeepRequest" } },
            },
          },
          responses: vaultResponses("VaultKeepResponse"),
        },
      },
      "/vault/records": {
        get: {
          operationId: "getVaultRecords",
          summary: "Undelivered bodies of the caller's own mailboxes",
          parameters: [
            {
              name: "limit",
              in: "query",
              schema: { type: "integer", minimum: 1, maximum: 100, default: 50 },
            },
            { name: "cursor", in: "query", schema: { type: "string" } },
          ],
          responses: vaultResponses("VaultRecordsResponse"),
        },
      },
      "/vault/refetch": {
        post: {
          operationId: "refetchVaultMail",
          summary:
            "Fetch the caller's own mail again from the provider (after a lost vault); one run per mailbox per day",
          responses: vaultResponses("VaultRefetchResponse"),
        },
      },
      "/vault/ack": {
        post: {
          operationId: "ackVaultRecords",
          requestBody: {
            required: true,
            content: {
              "application/json": {
                schema: { $ref: "#/components/schemas/VaultAckRequest" },
              },
            },
          },
          responses: vaultResponses("VaultAckResponse"),
        },
      },
      "/workspaces/{workspaceId}/routines": {
        get: {
          operationId: "getRoutines",
          summary: "Workspace routines with their newest scheduled run",
          parameters: [workspaceId],
          responses: clientResponses("RoutinesResponse"),
        },
      },
      "/workspaces/{workspaceId}/routines/{kind}": {
        put: {
          operationId: "setRoutineEnabled",
          summary: "Enable or disable a routine through the audited caller RPC",
          parameters: [
            workspaceId,
            {
              name: "kind",
              in: "path",
              required: true,
              schema: schema(routineKind),
            },
          ],
          requestBody: {
            required: true,
            content: {
              "application/json": {
                schema: { $ref: "#/components/schemas/RoutineEnabledRequest" },
              },
            },
          },
          responses: clientResponses("MutationResponse"),
        },
      },
      "/workspaces/{workspaceId}/decisions": {
        get: {
          operationId: "getDecisions",
          parameters: [
            workspaceId,
            {
              name: "status",
              in: "query",
              schema: { type: "string", enum: ["new", "done", "dismissed", "all"], default: "new" },
            },
            {
              name: "limit",
              in: "query",
              schema: { type: "integer", minimum: 1, maximum: 100, default: 50 },
            },
            { name: "cursor", in: "query", schema: { type: "string" } },
            {
              name: "importance",
              in: "query",
              schema: { type: "string", enum: [...cardImportance.options] },
            },
            {
              name: "kind",
              in: "query",
              schema: { type: "string", enum: [...decisionKind.options] },
            },
          ],
          responses: clientResponses("DecisionsResponse"),
        },
      },
      ...Object.fromEntries(
        [
          [
            "/decisions/{cardId}/feedback",
            "post",
            "recordDecisionFeedback",
            "cardId",
            "FeedbackRequest",
          ],
          [
            "/clients/{clientId}/outcomes",
            "post",
            "recordClientOutcome",
            "clientId",
            "OutcomeRequest",
          ],
          ["/outcomes/{outcomeId}", "delete", "deleteClientOutcome", "outcomeId", null],
        ].map(([path, method, operationId, id, request]) => [
          `/workspaces/{workspaceId}${path}`,
          {
            [String(method)]: {
              operationId,
              parameters: [
                workspaceId,
                {
                  name: id,
                  in: "path",
                  required: true,
                  schema: { type: "string", format: "uuid" },
                },
              ],
              ...(request
                ? {
                    requestBody: {
                      required: true,
                      content: {
                        "application/json": { schema: { $ref: `#/components/schemas/${request}` } },
                      },
                    },
                  }
                : {}),
              responses: {
                ...(method === "delete"
                  ? Object.fromEntries(
                      Object.entries(clientResponses("MutationResponse")).filter(
                        ([status]) => status !== "200",
                      ),
                    )
                  : clientResponses("MutationResponse")),
                "404": errorResponse("Target not found or not visible"),
                ...(method === "delete" ? { "204": { description: "Outcome deleted" } } : {}),
              },
            },
          },
        ]),
      ),
      "/workspaces/{workspaceId}/replay/runs": {
        get: {
          operationId: "getReplayRuns",
          parameters: [workspaceId],
          responses: clientResponses("ReplayRunsResponse"),
        },
      },
      "/workspaces/{workspaceId}/replay/runs/{runId}/clients/{clientId}": {
        get: {
          operationId: "getReplayClient",
          parameters: [
            workspaceId,
            ...["runId", "clientId"].map((name) => ({
              name,
              in: "path",
              required: true,
              schema: { type: "string", format: "uuid" },
            })),
          ],
          responses: {
            ...clientResponses("ReplayClientResponse"),
            "404": errorResponse("Narrative not found or not visible"),
          },
        },
      },
      ...Object.fromEntries(
        ["timeline", "evidence"].map((kind) => [
          `/workspaces/{workspaceId}/clients/{clientId}/${kind}`,
          {
            get: {
              operationId: kind === "timeline" ? "getClientTimeline" : "getClientEvidence",
              parameters: [
                workspaceId,
                {
                  name: "clientId",
                  in: "path",
                  required: true,
                  schema: { type: "string", format: "uuid" },
                },
                {
                  name: "limit",
                  in: "query",
                  schema: { type: "integer", minimum: 1, maximum: 100, default: 50 },
                },
                { name: "cursor", in: "query", schema: { type: "string" } },
              ],
              responses: {
                ...clientResponses(kind === "timeline" ? "TimelineResponse" : "EvidenceResponse"),
                "404": errorResponse("Client not found or not visible"),
              },
            },
          },
        ]),
      ),
      "/workspaces/{workspaceId}/evidence/{evidenceId}": {
        get: {
          operationId: "getEvidence",
          parameters: [
            workspaceId,
            {
              name: "evidenceId",
              in: "path",
              required: true,
              schema: { type: "string", format: "uuid" },
            },
          ],
          responses: {
            ...clientResponses("EvidenceItem"),
            "404": errorResponse("Evidence not found or not visible"),
          },
        },
      },
      "/workspaces/{workspaceId}/clients": {
        get: {
          operationId: "getClients",
          summary: "Visible client portfolio, sorted by name (maximum 500)",
          parameters: [
            workspaceId,
            {
              name: "status",
              in: "query",
              required: false,
              schema: { type: "string", enum: ["active", "churned", "all"], default: "active" },
            },
          ],
          responses: clientResponses("ClientsResponse"),
        },
      },
      "/workspaces/{workspaceId}/clients/{clientId}": {
        get: {
          operationId: "getClientDetail",
          summary: "Current client picture with up to 20 live state changes",
          parameters: [
            workspaceId,
            {
              name: "clientId",
              in: "path",
              required: true,
              schema: { type: "string", format: "uuid" },
            },
          ],
          responses: {
            ...clientResponses("ClientDetailResponse"),
            "404": errorResponse("Client not found or not visible"),
          },
        },
      },
      "/workspaces/{workspaceId}/meetings/upcoming": {
        get: {
          operationId: "getUpcomingMeetings",
          summary: "Pre-meeting briefs for client meetings that have not started, soonest first",
          parameters: [workspaceId],
          responses: {
            "200": {
              description: "At most 10 briefs; metadata_only clients carry counts only",
              content: {
                "application/json": {
                  schema: { $ref: "#/components/schemas/MeetingsResponse" },
                },
              },
            },
            "401": errorResponse("Missing or invalid token"),
            "403": errorResponse("Not an owner or admin of the workspace"),
            "429": errorResponse("Rate limited"),
            "503": errorResponse("Reads are not configured on this deployment"),
          },
        },
      },
      "/workspaces/{workspaceId}/changes": {
        get: {
          operationId: "getChanges",
          summary: "What changed across the workspace since a date, newest first",
          parameters: [
            workspaceId,
            {
              name: "since",
              in: "query",
              required: false,
              description: "ISO date, default 7 days ago, at most 31 days back",
              schema: { type: "string", pattern: "^\\d{4}-\\d{2}-\\d{2}$" },
            },
          ],
          responses: {
            "200": {
              description: "At most 300 changes; excluded clients absent, no text",
              content: {
                "application/json": {
                  schema: { $ref: "#/components/schemas/ChangesResponse" },
                },
              },
            },
            "422": errorResponse("Invalid since"),
            "401": errorResponse("Missing or invalid token"),
            "403": errorResponse("Not an owner or admin of the workspace"),
            "429": errorResponse("Rate limited"),
            "503": errorResponse("Reads are not configured on this deployment"),
          },
        },
      },
      "/workspaces/{workspaceId}/attention": {
        get: {
          operationId: "getAttention",
          summary: "Clients that need attention, most urgent first",
          parameters: [workspaceId],
          responses: {
            "200": {
              description: "Latest live assessment per client",
              content: {
                "application/json": {
                  schema: { $ref: "#/components/schemas/AttentionResponse" },
                },
              },
            },
            "401": errorResponse("Missing or invalid token"),
            "403": errorResponse("Not an owner or admin of the workspace"),
            "429": errorResponse("Rate limited"),
            "503": errorResponse("Reads are not configured on this deployment"),
          },
        },
      },
    },
    components: {
      securitySchemes: { bearer: { type: "http", scheme: "bearer" } },
      schemas: {
        VaultKeepRequest: schema(vaultKeepRequest),
        VaultKeepResponse: schema(vaultKeepResponse),
        VaultRecordsResponse: schema(vaultRecordsResponse),
        VaultAckRequest: schema(vaultAckRequest),
        VaultAckResponse: schema(vaultAckResponse),
        VaultRefetchResponse: schema(vaultRefetchResponse),
        DecisionsResponse: schema(decisionsResponse),
        FeedbackRequest: schema(feedbackRequest),
        OutcomeRequest: schema(outcomeRequest),
        MutationResponse: schema(mutationResponse),
        RoutinesResponse: schema(routinesResponse),
        RoutineEnabledRequest: schema(routineEnabledRequest, "input"),
        ReplayRunsResponse: schema(replayRunsResponse),
        ReplayClientResponse: schema(replayClientResponse),
        ApiError: schema(apiError),
        TimelineResponse: schema(timelineResponse),
        EvidenceResponse: schema(evidenceResponse),
        EvidenceItem: schema(evidenceItem),
        ClientsResponse: schema(clientsResponse),
        ClientDetailResponse: schema(clientDetailResponse),
        AttentionResponse: schema(attentionResponse),
        MeetingsResponse: schema(meetingsResponse),
        ChangesResponse: schema(changesResponse),
      },
    },
  };
  const paths: Record<string, Record<string, unknown>> = document.paths;
  const schemas: Record<string, unknown> = document.components.schemas;
  const pathName = (path: string) => path.replace(/:([A-Za-z]+Id)/g, "{$1}");
  const parameters = (path: string) =>
    [...path.matchAll(/\{([A-Za-z]+Id)\}/g)].map((match) => ({
      name: match[1],
      in: "path",
      required: true,
      schema: { type: "string", format: "uuid" },
    }));
  for (const route of setupWriteRoutes) {
    const path = pathName(
      "signedOnly" in route ? route.path : `/workspaces/{workspaceId}${route.path}`,
    );
    const response = "response" in route ? `${route.operation}Response` : null;
    const request = "request" in route ? `${route.operation}Request` : null;
    if ("response" in route) schemas[`${route.operation}Response`] = schema(route.response);
    if ("request" in route) schemas[`${route.operation}Request`] = schema(route.request, "input");
    paths[path] = {
      ...paths[path],
      [route.method.toLowerCase()]: {
        operationId: route.operation,
        description:
          "signedOnly" in route
            ? "Any signed-in user."
            : route.operation === "removeMember"
              ? "Owner only; the RPC also guards last-owner removal."
              : "Owner/admin setup operation. Writes authorize and audit through the caller RPC.",
        parameters: parameters(path),
        ...(request
          ? {
              requestBody: {
                required: true,
                content: {
                  "application/json": { schema: { $ref: `#/components/schemas/${request}` } },
                },
              },
            }
          : {}),
        responses: {
          ...Object.fromEntries(
            Object.entries(clientResponses(response ?? "MutationResponse")).filter(
              ([status]) => status !== "200" && !("signedOnly" in route && status === "403"),
            ),
          ),
          "404": errorResponse("Target not found or RPC refused"),
          ...(response
            ? {
                "200": {
                  description: "Setup mutation completed",
                  content: {
                    "application/json": { schema: { $ref: `#/components/schemas/${response}` } },
                  },
                },
              }
            : { "204": { description: "Setup mutation completed" } }),
        },
      },
    };
  }
  for (const route of setupReadRoutes) {
    const path = pathName(`/workspaces/{workspaceId}${route.path}`);
    const model = `${route.operation}SetupResponse`;
    schemas[model] = schema(route.response);
    paths[path] = {
      ...paths[path],
      get: {
        operationId: `getSetup${route.operation[0]?.toUpperCase()}${route.operation.slice(1)}`,
        parameters: [
          ...parameters(path),
          ...(route.operation === "audit"
            ? [
                {
                  name: "page",
                  in: "query",
                  schema: { type: "integer", minimum: 1, maximum: 200, default: 1 },
                },
              ]
            : []),
        ],
        responses: { ...clientResponses(model), "404": errorResponse("Target not found") },
      },
    };
  }
  return document;
}

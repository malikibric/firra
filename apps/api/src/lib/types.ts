import type { McpTokenRecord } from "../mcp/tokens.js";
import type { Logger } from "./logger.js";

export interface AuthUser {
  id: string;
  email: string | null;
}

export interface AppVariables {
  logger: Logger;
  user: AuthUser;
  internalCaller: string;
  requestId: string;
  mcpTokenId: string;
  mcpToken: McpTokenRecord;
}

export interface AppEnv {
  Variables: AppVariables;
}

import { OpenCode } from "@opencode/client"

/**
 * One client, same-origin: Vite's dev proxy forwards /api to the background
 * service with credentials, so the browser never handles a token. In a
 * production deploy behind a reverse proxy the same setup applies.
 */
export const client = OpenCode.make({ baseUrl: window.location.origin })

export type {
  SessionInfo,
  ModelInfo,
  AgentInfo,
  SessionMessageInfo,
  V2Event,
  ModelRef,
} from "@opencode/client"

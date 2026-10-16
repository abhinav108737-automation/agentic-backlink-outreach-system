// Types and channel names shared by main and preload. Contains no secrets and no Node/Electron imports.
export const CHANNELS = {
  request: "ken:request",
  backendStatus: "ken:backend-status",
  statusEvent: "ken:backend-status-event",
} as const;

export type BackendState = "starting" | "ready" | "failed" | "stopped";

/** Never includes the API token or the port-bound credentials. */
export interface BackendStatus {
  state: BackendState;
  error?: string;
}

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export interface KenRequest {
  method: HttpMethod;
  /** Relative API path only, e.g. "/health/ready". Absolute URLs are rejected. */
  path: string;
  body?: unknown;
}

export interface KenResponse {
  status: number;
  body: unknown;
  correlationId: string | null;
}

export interface KenApi {
  request(req: KenRequest): Promise<KenResponse>;
  backendStatus(): Promise<BackendStatus>;
  onBackendStatus(cb: (s: BackendStatus) => void): () => void;
}

/** First path segment must be one of the API areas (docs/14 C8). The renderer is not a generic
 *  HTTP client: anything outside these areas is refused before it reaches the token-bearing proxy. */
export const ALLOWED_ROOTS: ReadonlySet<string> = new Set([
  "health", "campaigns", "prospects", "messages", "threads", "backlinks", "reports", "experiments",
  "optimization-proposals", "approvals", "settings", "providers", "agent-runs", "system",
]);

const METHODS: readonly string[] = ["GET", "POST", "PUT", "PATCH", "DELETE"];
// One leading slash, then safe path/query characters. No scheme, no //, no "..", no backslash.
const SAFE_PATH = /^\/(?!\/)[A-Za-z0-9._~\-/?=&%:,@+]*$/;

export function validateRequest(req: unknown): KenRequest {
  if (typeof req !== "object" || req === null) throw new TypeError("request must be an object");
  const r = req as Record<string, unknown>;
  if (typeof r.method !== "string" || !METHODS.includes(r.method)) throw new TypeError("bad method");
  if (typeof r.path !== "string" || r.path.length > 2048 || !SAFE_PATH.test(r.path) || r.path.includes(".."))
    throw new TypeError("path must be a relative API path");
  // Encoded dots, slashes, backslashes or NULs could dodge a later route-based policy.
  if (/%2e|%2f|%5c|%00/i.test(r.path)) throw new TypeError("encoded path separators are not allowed");
  const root = r.path.split(/[/?]/)[1] ?? "";
  if (!ALLOWED_ROOTS.has(root)) throw new TypeError(`path root "${root}" is not an API area`);
  return { method: r.method as HttpMethod, path: r.path, body: r.body };
}

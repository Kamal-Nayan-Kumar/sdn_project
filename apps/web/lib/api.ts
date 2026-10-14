/**
 * Typed client for the FastAPI backend.
 *
 * Everything is fetched through the relative `/api` prefix: the Next.js app
 * owns a rewrite that proxies `/api/*` to the local backend on
 * `http://localhost:8000`, so the browser never talks to another origin and
 * no port needs to be baked into the frontend.
 *
 * Error handling is deliberate:
 *   * a non-JSON error body (an HTML 502 page, a proxy timeout page, an empty
 *     500) never throws a parse error at the call site -- `ApiError` carries
 *     the status plus whatever text the server did send;
 *   * a network-level failure (backend down) becomes `ApiError` with
 *     `status: 0` and `isNetworkError: true`, so the UI can say "the local lab
 *     or backend is unreachable" instead of guessing.
 */

import type {
  HealthResponse,
  LabStatusResponse,
  LinksPayload,
  NetworkSnapshot,
  PathCandidate,
  PathsResponse,
  RerouteRequest,
  RerouteResponse,
  ScenarioResetResponse,
  SnapshotResponse,
  TopologyResponse,
} from "./types";
import { linksArray } from "./derive";

/** Prefix for every call. Change it here if the rewrite target moves. */
export const API_BASE = "/api";

export class ApiError extends Error {
  readonly status: number;
  readonly statusText: string;
  /** Raw response text (JSON string or an HTML/proxy error page). */
  readonly bodyText: string;
  /** Parsed body when the server did send JSON. */
  readonly payload: unknown;
  readonly url: string;
  readonly isNetworkError: boolean;

  constructor(options: {
    status: number;
    statusText: string;
    bodyText: string;
    payload?: unknown;
    url: string;
    message?: string;
    isNetworkError?: boolean;
  }) {
    super(
      options.message ??
        `Request to ${options.url} failed with status ${options.status}${
          options.statusText ? ` (${options.statusText})` : ""
        }`,
    );
    this.name = "ApiError";
    this.status = options.status;
    this.statusText = options.statusText;
    this.bodyText = options.bodyText;
    this.payload = options.payload;
    this.url = options.url;
    this.isNetworkError = options.isNetworkError === true;
  }
}

/** Best-effort short description of a failed request, never a guess. */
export function describeApiError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.isNetworkError) {
      return "Cannot reach the local backend (is the API running?).";
    }
    const detail =
      typeof error.payload === "object" && error.payload !== null
        ? ((error.payload as Record<string, unknown>).detail ??
          (error.payload as Record<string, unknown>).message)
        : undefined;
    if (typeof detail === "string" && detail.length > 0) return detail;
    if (typeof detail === "object" && detail !== null) return JSON.stringify(detail);
    const snippet = error.bodyText.trim().slice(0, 200);
    if (snippet.length > 0) return `${error.status}: ${snippet}`;
    return `${error.status} ${error.statusText}`.trim();
  }
  if (error instanceof Error) return error.message;
  return String(error);
}

async function parseBody(response: Response): Promise<{ text: string; payload: unknown }> {
  const text = await response.text().catch(() => "");
  if (text.length === 0) return { text: "", payload: undefined };
  try {
    return { text, payload: JSON.parse(text) as unknown };
  } catch {
    return { text, payload: undefined };
  }
}

async function request<T>(
  path: string,
  init: RequestInit & { query?: Record<string, string | number | undefined> } = {},
): Promise<T> {
  const { query, ...rest } = init;
  const url = new URL(`${API_BASE}${path}`, typeof window === "undefined" ? "http://localhost" : window.location.origin);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, String(value));
    }
  }

  let response: Response;
  try {
    response = await fetch(url.toString(), {
      ...rest,
      headers: {
        Accept: "application/json",
        ...(rest.body ? { "Content-Type": "application/json" } : {}),
        ...(rest.headers ?? {}),
      },
    });
  } catch (cause) {
    // `fetch` only rejects on transport failures; the message is a browser
    // string like "Failed to fetch", which says nothing useful to an operator.
    const reason = cause instanceof Error ? cause.message : String(cause);
    throw new ApiError({
      status: 0,
      statusText: "",
      bodyText: reason,
      url: url.toString(),
      message: `Cannot reach ${path}: ${reason}`,
      isNetworkError: true,
    });
  }

  const { text, payload } = await parseBody(response);
  if (!response.ok) {
    throw new ApiError({
      status: response.status,
      statusText: response.statusText,
      bodyText: text,
      payload,
      url: url.toString(),
    });
  }
  return payload as T;
}

/* -------------------------------------------------------------------------- */
/* Endpoints                                                                  */
/* -------------------------------------------------------------------------- */

/** `GET /health` -- process-level health of the backend. */
export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/health");
}

/**
 * `GET /v1/status` -- local lab status: switch backend, connected switches,
 * which links currently carry a congestion generator.
 */
export function getStatus(): Promise<LabStatusResponse> {
  return request<LabStatusResponse>("/v1/status");
}

/**
 * `GET /v1/snapshot` -- one coherent view of the network.
 *
 * `links` is normalised to an array here (the lab serves it keyed by
 * `link_id`), but consumers should still call `linksArray()` -- the normaliser
 * is deliberately idempotent.
 */
export function getSnapshot(): Promise<NetworkSnapshot> {
  return request<SnapshotResponse>("/v1/snapshot").then((raw) => {
    const links: LinksPayload = linksArray(raw);
    return {
      topology: raw.topology ?? null,
      links,
      flows: raw.flows ?? [],
      congested_links: raw.congested_links ?? [],
      active_scenario: raw.active_scenario ?? null,
      controller_reachable: raw.controller_reachable,
      captured_at:
        typeof raw.captured_at === "string"
          ? raw.captured_at
          : typeof raw.measured_at === "number"
            ? new Date(raw.measured_at * 1000).toISOString()
            : typeof raw.captured_at === "number"
              ? new Date(raw.captured_at * 1000).toISOString()
              : new Date().toISOString(),
    } satisfies NetworkSnapshot;
  });
}

/** `GET /v1/topology` -- nodes and links as built, without telemetry. */
export function getTopology(): Promise<TopologyResponse> {
  return request<TopologyResponse>("/v1/topology");
}

/**
 * `GET /v1/paths?src&dst` -- candidate paths for a host pair.
 *
 * The lab answers `{paths: [...]}`, the backend's evaluate_paths tool answers
 * `{candidates: [...]}`; `pathCandidates()` accepts either.
 */
export function getPaths(src: string, dst: string): Promise<PathCandidate[]> {
  return request<PathsResponse>("/v1/paths", { query: { src, dst } }).then((raw) => {
    // Imported lazily to keep this module's import graph shallow.
    return require_pathCandidates(raw);
  });
}

/** `POST /v1/reroute` -- apply an already validated reroute. */
export function postReroute(body: RerouteRequest): Promise<RerouteResponse> {
  return request<RerouteResponse>("/v1/reroute", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** `POST /v1/scenarios/reset` -- restore the lab baseline. */
export function resetScenario(): Promise<ScenarioResetResponse> {
  return request<ScenarioResetResponse>("/v1/scenarios/reset", { method: "POST", body: "{}" });
}

/* -------------------------------------------------------------------------- */
/* Run stream (spec section 12: GET /api/runs/{id}/events, POST .../cancel)   */
/* -------------------------------------------------------------------------- */

/**
 * SSE endpoint for a run.
 *
 * `sinceSeq` is sent as a query parameter on every (re)connect: `EventSource`
 * cannot set request headers, so this is how the server knows which events it
 * has already delivered and can resume instead of replaying from 0. Assume the
 * query parameter is named after the field; if the backend names it
 * differently, change it in this one place.
 */
export function runEventsUrl(runId: string, sinceSeq = 0): string {
  const query = new URLSearchParams({ since_seq: String(Math.max(0, Math.floor(sinceSeq))) });
  return `${API_BASE}/runs/${encodeURIComponent(runId)}/events?${query.toString()}`;
}

/** `GET /api/runs/{id}` -- persisted run state, used to rehydrate after a
 *  reconnect (or to load history without replaying a stream). */
export function getRun(runId: string): Promise<unknown> {
  return request<unknown>(`/runs/${encodeURIComponent(runId)}`);
}

/** `POST /api/runs/{id}/cancel` -- request a safe cancellation. */
export function cancelRun(runId: string): Promise<unknown> {
  return request<unknown>(`/runs/${encodeURIComponent(runId)}/cancel`, {
    method: "POST",
    body: "{}",
  });
}

/** `POST /api/conversations/{id}/messages` -- submit a message, start a run. */
export function postConversationMessage(
  conversationId: string | null,
  body: Record<string, unknown>,
): Promise<unknown> {
  const path = conversationId
    ? `/conversations/${encodeURIComponent(conversationId)}/messages`
    : "/conversations";
  return request<unknown>(path, { method: "POST", body: JSON.stringify(body) });
}

/** Named so the indirection above reads clearly at the call site. */
function require_pathCandidates(raw: PathsResponse): PathCandidate[] {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  return pathCandidatesImpl(raw);
}

import { pathCandidates as pathCandidatesImpl } from "./derive";

/**
 * `useAgentRun` -- SSE subscription for one agent run.
 *
 * Contract (spec section 10):
 *   * the stream is opened against `GET /api/runs/{id}/events`;
 *   * every frame is validated with `normaliseAgentEvent`, so a malformed
 *     frame is counted and dropped instead of rendering as garbage;
 *   * duplicates are ignored by the reducer (keyed on `(run_id, seq)`);
 *   * the stream is closed and reopened with exponential backoff on transport
 *     failure, resuming from the last sequence number the client saw;
 *   * a terminal event (`run_completed` / `run_failed` / `run_cancelled`)
 *     closes the stream for good;
 *   * `stop()` / `cancel()` close the `EventSource` and record why.
 *
 * The reducer and the frame parser live in `./agentRun` so they can be unit
 * tested without React or an `EventSource` implementation; the hook itself is
 * a thin shell around them.
 */

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { AgentRunEvent, FinalOutcome } from "./types";
import {
  agentRunGroups,
  agentRunReducer,
  hydrateAgentRun,
  initialAgentRunState,
  isAgentRunFinished,
  normaliseAgentEvent,
  stateFromRawEvents,
  TERMINAL_EVENT_TYPES,
  type AgentRunState,
} from "./agentRun";
import { cancelRun as apiCancelRun, describeApiError } from "./api";

export type { AgentRunState, RunStatus } from "./agentRun";
export {
  agentRunGroups,
  agentRunReducer,
  canCancelRun,
  hydrateAgentRun,
  initialAgentRunState,
  isAgentRunFinished,
  isTerminalEvent,
  TERMINAL_EVENT_TYPES,
  normaliseAgentEvent,
} from "./agentRun";
export type { ActivityGroup } from "./types";

export interface UseAgentRunOptions {
  /** SSE endpoint. When omitted (or empty) nothing is opened. */
  url?: string | null;
  /** Run id, used for the cancel request and for labels. */
  runId?: string | null;
  /** Open the stream on mount. Default `true`. */
  enabled?: boolean;
  /** Reconnect with backoff after a transport error. Default `true`. */
  doReconnect?: boolean;
  /** Give up after this many consecutive failures. Default 8. */
  maxRetries?: number;
  /** First backoff delay in ms. Default 1000. */
  baseDelayMs?: number;
  /** Backoff ceiling in ms. Default 30000. */
  maxDelayMs?: number;
  /** Called with each accepted event, after it has been applied. */
  onEvent?: (event: AgentRunEvent) => void;
  /** Called once, when the run reaches a terminal event. */
  onTerminal?: (state: AgentRunState) => void;
  /** Called with a human-readable message when the stream drops. */
  onError?: (message: string) => void;
}

export interface UseAgentRunResult {
  /** Folded run state (events, tool calls, evidence, gates, verification). */
  state: AgentRunState;
  events: readonly AgentRunEvent[];
  groups: ReturnType<typeof agentRunGroups>;
  status: AgentRunState["status"];
  outcome: FinalOutcome | null;
  finished: boolean;
  /** Stream health. `false` between a drop and the next successful open. */
  connected: boolean;
  /** Set after a drop; cleared on the next successful open. */
  interrupted: boolean;
  /** Last stream error, human readable. */
  error: string | null;
  /** Consecutive failed doReconnect attempts. */
  retries: number;
  /** True while a cancel request is in flight. */
  cancelling: boolean;
  start: () => void;
  stop: (reason?: string) => void;
  reset: () => void;
  doReconnect: () => void;
  /** Ask the backend for a safe cancellation, then close the stream. */
  cancel: (reason?: string) => Promise<void>;
}

const DEFAULT_MAX_RETRIES = 8;
const DEFAULT_BASE_DELAY_MS = 1000;
const DEFAULT_MAX_DELAY_MS = 30000;

/** Small jitter so a room full of dashboards does not doReconnect in lockstep. */
function jitter(ms: number): number {
  return ms + Math.floor(Math.random() * Math.min(1000, ms));
}

export function useAgentRun(options: UseAgentRunOptions = {}): UseAgentRunResult {
  const {
    url = null,
    runId = null,
    enabled = true,
    doReconnect = true,
    maxRetries = DEFAULT_MAX_RETRIES,
    baseDelayMs = DEFAULT_BASE_DELAY_MS,
    maxDelayMs = DEFAULT_MAX_DELAY_MS,
    onEvent,
    onTerminal,
    onError,
  } = options;

  const [state, setState] = useState<AgentRunState>(initialAgentRunState);
  const [connected, setConnected] = useState(false);
  const [interrupted, setInterrupted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retries, setRetries] = useState(0);
  const [cancelling, setCancelling] = useState(false);
  /** Forces a doReconnect even when `url` has not changed. */
  const [attempt, setAttempt] = useState(0);

  const sourceRef = useRef<EventSource | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);
  const seqRef = useRef(0);
  const retriesRef = useRef(0);
  const stoppedRef = useRef(false);
  const terminalRef = useRef(false);

  // Latest callbacks, so changing them never re-opens the stream.
  const onEventRef = useRef(onEvent);
  const onTerminalRef = useRef(onTerminal);
  const onErrorRef = useRef(onError);
  onEventRef.current = onEvent;
  onTerminalRef.current = onTerminal;
  onErrorRef.current = onError;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const closeSource = useCallback(() => {
    const source = sourceRef.current;
    sourceRef.current = null;
    if (source) {
      source.onopen = null;
      source.onerror = null;
      source.onmessage = null;
      source.close();
    }
  }, []);

  const stop = useCallback(
    (reason?: string) => {
      stoppedRef.current = true;
      clearTimer();
      closeSource();
      if (mountedRef.current) {
        setConnected(false);
        if (reason) setError(reason);
      }
    },
    [clearTimer, closeSource],
  );

  const start = useCallback(() => {
    stoppedRef.current = false;
    terminalRef.current = false;
    retriesRef.current = 0;
    if (mountedRef.current) setRetries(0);
    setAttempt((n) => n + 1);
  }, []);

  const reset = useCallback(() => {
    stoppedRef.current = true;
    terminalRef.current = false;
    retriesRef.current = 0;
    clearTimer();
    closeSource();
    seqRef.current = 0;
    setState(initialAgentRunState);
    setConnected(false);
    setInterrupted(false);
    setError(null);
    setRetries(0);
  }, [clearTimer, closeSource]);

  const handleReconnect = useCallback(() => {
    stoppedRef.current = false;
    retriesRef.current = 0;
    if (mountedRef.current) setRetries(0);
    setAttempt((n) => n + 1);
  }, []);

  const cancel = useCallback(
    async (reason?: string) => {
      if (!runId) {
        stop(reason ?? "No run to cancel");
        return;
      }
      setCancelling(true);
      try {
        await apiCancelRun(runId);
      } catch (cause) {
        // A failed cancel request is reported, never silently swallowed: the
        // operator must know whether the run is actually stopping.
        if (mountedRef.current) setError(describeApiError(cause));
      } finally {
        if (mountedRef.current) setCancelling(false);
      }
      stoppedRef.current = true;
      clearTimer();
      closeSource();
      if (mountedRef.current) {
        setConnected(false);
        setInterrupted(true);
        setError(reason ?? "Cancellation requested");
      }
    },
    [clearTimer, closeSource, runId, stop],
  );

  // The stream itself.
  useEffect(() => {
    if (!enabled || !url) return;
    if (typeof EventSource === "undefined") {
      setError("This browser does not support Server-Sent Events.");
      return;
    }
    if (stoppedRef.current) return;

    const base = url.split("?")[0];
    const query = new URLSearchParams(url.includes("?") ? url.slice(url.indexOf("?") + 1) : "");
    // Always resume from the newest sequence number this client has applied,
    // so a doReconnect is a resume rather than a full replay.
    query.set("since_seq", String(Math.max(0, Math.floor(seqRef.current))));
    const source = new EventSource(`${base}?${query.toString()}`, { withCredentials: false });
    sourceRef.current = source;

    const handleOpen = (): void => {
      if (!mountedRef.current) return;
      retriesRef.current = 0;
      setConnected(true);
      setInterrupted(false);
      setError(null);
      setRetries(0);
    };

    const handleMessage = (message: MessageEvent<string>): void => {
      let frame: unknown = null;
      try {
        frame = typeof message.data === "string" ? JSON.parse(message.data) : message.data;
      } catch {
        frame = null;
      }
      const event = normaliseAgentEvent(frame, seqRef.current + 1);
      if (!event) {
        if (mountedRef.current) {
          setState((prev) => ({ ...prev, droppedCount: prev.droppedCount + 1 }));
        }
        return;
      }
      if (event.seq > seqRef.current) seqRef.current = event.seq;
      if (mountedRef.current) {
        setState((prev) => agentRunReducer(prev, event));
        onEventRef.current?.(event);
      }
      if (TERMINAL_EVENT_TYPES.includes(event.type)) {
        terminalRef.current = true;
        const source2 = sourceRef.current;
        if (source2 === source) {
          source.onopen = null;
          source.onerror = null;
          source.onmessage = null;
          source.close();
          sourceRef.current = null;
        }
        if (mountedRef.current) setConnected(false);
      }
    };

    const handleError = (): void => {
      if (!mountedRef.current) return;
      setConnected(false);
      setInterrupted(true);
      // EventSource retries on its own for transport errors, but a closed
      // stream (404, proxy gone, body ended) never will. Either way we drive
      // the backoff ourselves so the resume point stays explicit.
      if (sourceRef.current === source) closeSource();

      if (terminalRef.current) {
        setError("Stream ended after the run finished.");
        return;
      }
      if (!doReconnect) {
        setError("Stream interrupted and doReconnect is disabled.");
        onErrorRef.current?.("Stream interrupted and doReconnect is disabled.");
        return;
      }
      const nextRetry = retriesRef.current + 1;
      retriesRef.current = nextRetry;
      setRetries(nextRetry);
      if (nextRetry > maxRetries) {
        const message = `Stream interrupted after ${nextRetry - 1} retries; giving up.`;
        setError(message);
        onErrorRef.current?.(message);
        return;
      }
      const delay = jitter(Math.min(maxDelayMs, baseDelayMs * 2 ** (nextRetry - 1)));
      const message = `Stream interrupted (attempt ${nextRetry}); reconnecting in ${Math.round(delay)} ms from sequence ${seqRef.current}.`;
      setError(message);
      onErrorRef.current?.(message);
      clearTimer();
      timerRef.current = setTimeout(() => {
        if (!mountedRef.current || stoppedRef.current) return;
        if (sourceRef.current !== null) closeSource();
        setAttempt((n) => n + 1);
      }, delay);
    };

    source.onopen = handleOpen;
    source.onmessage = handleMessage;
    source.onerror = handleError;

    return () => {
      clearTimer();
      const current = sourceRef.current;
      if (current === source) {
        source.onopen = null;
        source.onerror = null;
        source.onmessage = null;
        source.close();
        sourceRef.current = null;
      }
    };
  }, [attempt, baseDelayMs, clearTimer, closeSource, enabled, maxDelayMs, maxRetries, doReconnect, url]);

  // Fire the terminal callback once, from the settled state.
  const terminalFiredRef = useRef(false);
  useEffect(() => {
    if (isAgentRunFinished(state) && !terminalFiredRef.current) {
      terminalFiredRef.current = true;
      onTerminalRef.current?.(state);
    }
    if (!isAgentRunFinished(state)) terminalFiredRef.current = false;
  }, [state]);

  const groups = useMemo(() => agentRunGroups(state.events), [state.events]);

  return {
    state,
    events: state.events,
    groups,
    status: state.status,
    outcome: state.outcome,
    finished: isAgentRunFinished(state),
    connected,
    interrupted,
    error,
    retries,
    cancelling,
    start,
    stop,
    reset,
    doReconnect: handleReconnect,
    cancel,
  };
}

/** Exported for tests: fold a persisted event list back into state. */
export async function rehydrateRun(
  runId: string,
  fetchRun: (id: string) => Promise<unknown>,
): Promise<AgentRunState> {
  const raw = await fetchRun(runId);
  const events =
    raw && typeof raw === "object" && Array.isArray((raw as Record<string, unknown>).events)
      ? ((raw as Record<string, unknown>).events as unknown[])
      : [];
  // Frames are validated one by one; unusable frames are counted and
  // dropped rather than guessed at (mirrors the live stream).
  return stateFromRawEvents(events);
}

"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { ConnectionState, IsoDateTime, LabHealth, NetworkSnapshot } from "@/types";

/**
 * Single polling point for the dashboard shell.
 *
 * The frontend never talks to the SDN controller. Everything comes from the
 * FastAPI backend (proxied by `next.config.ts`), and every failure is
 * reported as the state it actually is: `offline` when the backend cannot be
 * reached at all, `degraded` when it answers but a dependency it needs is
 * missing. The two are never collapsed into a single "error".
 *
 * NOTE (ownership): agent B owns `src/lib/**` and may supply the real
 * telemetry/SSE hooks there. When that lands, replace the two `useEffect`
 * pollers below with the hooks from `@/lib` and keep this context's shape —
 * the header, rail, metric strip and sidebar all read it through
 * `useLabData()` and do not care where the values come from.
 */

const HEALTH_PATH = "/backend/health";
const SNAPSHOT_PATH = "/api/lab/snapshot";
const HEALTH_INTERVAL_MS = 5000;
const SNAPSHOT_INTERVAL_MS = 3000;

/** Expected backend routes for the lab controls. Adjust in this one place. */
const RESET_PATH = "/api/lab/reset";
const CONGESTION_PATH = "/api/lab/scenario/congestion";
const MONITOR_PATH = "/api/lab/scenario/monitor";

export interface LabActionState {
  status: "idle" | "running" | "done" | "error";
  message: string | null;
}

const IDLE_ACTION: LabActionState = { status: "idle", message: null };

interface LabDataValue {
  connection: ConnectionState;
  connectionDetail: string;
  health: LabHealth | null;
  snapshot: NetworkSnapshot | null;
  snapshotDetail: string;
  scenario: string | null;
  labMode: string | null;
  lastSnapshotAt: IsoDateTime | null;
  reset: LabActionState;
  congestion: LabActionState;
  monitor: LabActionState;
  resetLab: () => Promise<void>;
  createCongestion: () => Promise<void>;
  startMonitor: () => Promise<void>;
  refresh: () => void;
}

const LabDataContext = createContext<LabDataValue | null>(null);

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    signal,
    cache: "no-store",
    headers: { accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`${path} returned HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

async function postAction(path: string, signal?: AbortSignal): Promise<string> {
  const response = await fetch(path, {
    method: "POST",
    signal,
    cache: "no-store",
    headers: { accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`${path} returned HTTP ${response.status}`);
  }
  const text = await response.text();
  return text.length > 0 ? text : "accepted";
}

export function LabDataProvider({ children }: { children: ReactNode }) {
  const [health, setHealth] = useState<LabHealth | null>(null);
  const [snapshot, setSnapshot] = useState<NetworkSnapshot | null>(null);
  const [snapshotDetail, setSnapshotDetail] = useState<string>("waiting for the first poll");
  const [pollNonce, setPollNonce] = useState(0);
  const [reset, setReset] = useState<LabActionState>(IDLE_ACTION);
  const [congestion, setCongestion] = useState<LabActionState>(IDLE_ACTION);
  const [monitor, setMonitor] = useState<LabActionState>(IDLE_ACTION);

  const refresh = useCallback(() => setPollNonce((n) => n + 1), []);

  // --- backend health -----------------------------------------------------
  useEffect(() => {
    const controller = new AbortController();
    let stopped = false;

    const poll = async () => {
      try {
        const next = await getJson<LabHealth>(HEALTH_PATH, controller.signal);
        if (!stopped) setHealth(next);
      } catch (error) {
        if (!stopped && (error as Error)?.name !== "AbortError") setHealth(null);
      }
    };

    void poll();
    const timer = window.setInterval(poll, HEALTH_INTERVAL_MS);
    return () => {
      stopped = true;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [pollNonce]);

  // --- live network snapshot ---------------------------------------------
  useEffect(() => {
    const controller = new AbortController();
    let stopped = false;

    const poll = async () => {
      try {
        const next = await getJson<NetworkSnapshot>(SNAPSHOT_PATH, controller.signal);
        if (stopped) return;
        setSnapshot(next);
        setSnapshotDetail(`captured ${next.captured_at}`);
      } catch (error) {
        if (stopped) return;
        const name = (error as Error)?.name;
        if (name === "AbortError") return;
        setSnapshot(null);
        setSnapshotDetail(name === "TypeError" ? "backend unreachable" : `snapshot error: ${String(error)}`);
      }
    };

    void poll();
    const timer = window.setInterval(poll, SNAPSHOT_INTERVAL_MS);
    return () => {
      stopped = true;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [pollNonce]);

  const runAction = useCallback(
    async (path: string, apply: (state: LabActionState) => void, verb: string) => {
      apply({ status: "running", message: null });
      try {
        const detail = await postAction(path);
        apply({ status: "done", message: detail });
        refresh();
      } catch (error) {
        apply({ status: "error", message: `${verb} failed — ${String(error)}` });
      }
    },
    [refresh],
  );

  const resetLab = useCallback(
    () => runAction(RESET_PATH, setReset, "Reset lab"),
    [runAction],
  );
  const createCongestion = useCallback(
    () => runAction(CONGESTION_PATH, setCongestion, "Create congestion"),
    [runAction],
  );
  const startMonitor = useCallback(
    () => runAction(MONITOR_PATH, setMonitor, "Start monitoring"),
    [runAction],
  );

  const value = useMemo<LabDataValue>(() => {
    const backendDown = health === null;
    const connection: ConnectionState = backendDown
      ? "offline"
      : health.degraded
        ? "degraded"
        : "connected";
    const connectionDetail = backendDown
      ? "FastAPI unreachable at 127.0.0.1:8000"
      : health.degraded
        ? "backend up, a dependency is degraded"
        : "backend and lab reachable";

    return {
      connection,
      connectionDetail,
      health,
      snapshot,
      snapshotDetail,
      scenario: snapshot?.active_scenario ?? null,
      labMode: health?.lab_mode ?? null,
      lastSnapshotAt: snapshot?.captured_at ?? null,
      reset,
      congestion,
      monitor,
      resetLab,
      createCongestion,
      startMonitor,
      refresh,
    };
  }, [congestion, health, monitor, refresh, reset, resetLab, createCongestion, startMonitor, snapshot, snapshotDetail]);

  return <LabDataContext.Provider value={value}>{children}</LabDataContext.Provider>;
}

export function useLabData(): LabDataValue {
  const value = useContext(LabDataContext);
  if (!value) {
    throw new Error("useLabData must be used inside <LabDataProvider>");
  }
  return value;
}

/** Keeps a value from being rendered as a fabricated number. */
export function useLatest<T>(value: T): T {
  const ref = useRef(value);
  ref.current = value;
  return ref.current;
}

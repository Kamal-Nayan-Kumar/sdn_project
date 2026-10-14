"use client";

import { IconReset, NetMindMark } from "./icons";
import { useLabData } from "./lab-data";
import type { AgentRunStatus, ConnectionState } from "@/types";

/**
 * Top bar: identity, lab connection, agent status, current scenario and the
 * reset-lab control (spec section 9, "Header").
 */

const CONNECTION_COPY: Record<ConnectionState, { dot: string; label: string }> = {
  connecting: { dot: "dot--info", label: "Contacting backend" },
  connected: { dot: "dot--ok", label: "Network lab connected" },
  degraded: { dot: "dot--warn", label: "Lab degraded" },
  offline: { dot: "dot--bad", label: "Lab offline" },
};

const AGENT_COPY: Record<AgentRunStatus, { dot: string; label: string }> = {
  idle: { dot: "dot--idle", label: "Agent idle" },
  waiting_for_clarification: { dot: "dot--warn", label: "Waiting for clarification" },
  running: { dot: "dot--info dot--live", label: "Agent running" },
  gathering_evidence: { dot: "dot--info dot--live", label: "Gathering evidence" },
  insufficient_evidence: { dot: "dot--warn", label: "Insufficient evidence" },
  action_proposed: { dot: "dot--info", label: "Action proposed" },
  awaiting_approval: { dot: "dot--warn", label: "Awaiting approval" },
  executing: { dot: "dot--info dot--live", label: "Executing" },
  verifying: { dot: "dot--info dot--live", label: "Verifying recovery" },
  complete: { dot: "dot--ok", label: "Run complete" },
  failed: { dot: "dot--bad", label: "Run failed" },
  cancelled: { dot: "dot--idle", label: "Run cancelled" },
};

/**
 * The shell has no live run of its own yet — the chat/agent wiring belongs to
 * agent B. Until then this is honestly "idle"; swap the value for the run
 * store from `@/lib` when it lands. Do not invent a running state.
 */
const AGENT_STATUS: AgentRunStatus = "idle";

export function Topbar() {
  const { connection, connectionDetail, scenario, labMode, resetLab, reset } = useLabData();
  const lab = CONNECTION_COPY[connection];
  const agent = AGENT_COPY[AGENT_STATUS];
  const busy = reset.status === "running";

  return (
    <header className="app-topbar">
      <div className="flex items-center gap-2.5">
        <NetMindMark />
        <span className="flex flex-col leading-none">
          <strong className="text-[15px] font-semibold tracking-[-0.01em]">NetMind</strong>
          <span className="mt-[3px] text-[11.5px] text-[var(--ink-3)]">Agentic control for SDN</span>
        </span>
      </div>

      <span aria-hidden="true" className="ml-1 h-6 w-px bg-[var(--line)]" />

      <p className="pill pill--plain" title={connectionDetail}>
        <span className={`dot ${lab.dot}`} aria-hidden="true" />
        {lab.label}
      </p>

      <p className="pill pill--plain" data-testid="agent-status">
        <span className={`dot ${agent.dot}`} aria-hidden="true" />
        {agent.label}
      </p>

      <p className="pill pill--plain mono" data-testid="scenario">
        <span className="text-[var(--ink-4)]">scenario</span>
        <span className={scenario ? "" : "text-[var(--ink-4)]"}>{scenario ?? "none"}</span>
      </p>

      {labMode ? (
        <p className="pill pill--plain mono" title="Backend LAB_MODE">
          <span className="text-[var(--ink-4)]">mode</span>
          {labMode}
        </p>
      ) : null}

      <span className="flex-1" />

      <span role="status" aria-live="polite" className="max-w-[280px] truncate text-[11.5px] text-[var(--ink-3)]">
        {reset.status === "error"
          ? reset.message
          : reset.status === "done"
            ? "Lab reset requested"
            : ""}
      </span>

      <button
        type="button"
        className="btn"
        onClick={() => void resetLab()}
        disabled={busy}
        title="Return the lab to its known baseline and clear the active scenario"
      >
        <IconReset size={14} />
        {busy ? "Resetting…" : "Reset lab"}
      </button>
    </header>
  );
}

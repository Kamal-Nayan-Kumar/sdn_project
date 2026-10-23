"use client";
import React from "react";

import { IconReset, NetMindMark } from "./icons";
import { useLabData } from "./lab-data";
import type { AgentRunStatus, ConnectionState } from "@/types";

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

export function Topbar() {
  const { connection, scenario, resetLab, reset, agentStatus } = useLabData();
  const lab = CONNECTION_COPY[connection];
  const busy = reset.status === "running";
  const agentCopy = AGENT_COPY[agentStatus] || { dot: "dot--idle", label: `Agent ${agentStatus}` };

  return (
    <header className="app-topbar flex items-center justify-between px-4 py-2 border-b border-[var(--line)] bg-[var(--surface)]">
      {/* Left: Brand Identity */}
      <div className="flex items-center gap-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-[var(--blue-soft)]">
          <NetMindMark size={24} />
        </div>
        <div className="flex flex-col leading-tight">
          <strong className="text-[15px] font-semibold text-[var(--ink)] tracking-tight">NetMind</strong>
          <span className="text-[11px] text-[var(--ink-3)] font-medium">Agentic control for SDN</span>
        </div>
      </div>

      {/* Center: System Status Summary */}
      <div className="hidden md:flex items-center gap-2 px-3 py-1 rounded-md bg-[var(--canvas)] border border-[var(--line)] text-[11.5px] text-[var(--ink-2)]">
        <span className="font-medium text-[var(--ink)]">Network Status:</span>
        <span>{scenario ? `Active Scenario (${scenario})` : "Baseline Traffic"}</span>
      </div>

      {/* Right: Lab connection indicator, agent status, scenario, reset */}
      <div className="flex items-center gap-3 text-[12px]">
        <div className="flex items-center gap-1.5 rounded-full bg-[var(--green-soft)] px-2.5 py-1 text-[var(--green-state)] font-medium">
          <span className={`h-2 w-2 rounded-full ${lab.dot === "dot--ok" ? "bg-[var(--green-state)]" : "bg-[var(--red-state)]"}`} />
          <span>{lab.label}</span>
        </div>

        <div className="flex items-center gap-1.5 rounded-full bg-[var(--sunken)] px-2.5 py-1 text-[var(--ink-2)] font-medium">
          <span className={`h-2 w-2 rounded-full ${agentStatus === "idle" ? "bg-[var(--ink-4)]" : agentStatus === "complete" ? "bg-[var(--green-state)]" : "bg-[var(--brand)] animate-pulse"}`} />
          <span>{agentCopy.label}</span>
        </div>

        {scenario && (
          <span className="rounded bg-[var(--red-soft)] text-[var(--red-state)] px-2 py-0.5 font-mono text-[11px] font-medium border border-[var(--red-state)]">
            scenario: {scenario}
          </span>
        )}

        <button
          type="button"
          onClick={() => void resetLab()}
          disabled={busy}
          className="flex items-center gap-1.5 rounded border border-[var(--line-strong)] bg-[var(--surface)] px-2.5 py-1 text-[11.5px] font-medium text-[var(--ink-2)] hover:bg-[var(--sunken)] transition-colors"
          title="Reset network to baseline"
        >
          <IconReset size={13} />
          <span>{busy ? "Resetting…" : "Reset Lab"}</span>
        </button>
      </div>
    </header>
  );
}

"use client";
import React from "react";

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
export function Topbar() {
  const { connection, connectionDetail, scenario, labMode, resetLab, reset } = useLabData();
  const lab = CONNECTION_COPY[connection];
  const busy = reset.status === "running";

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

      {/* Center: Global Search Input */}
      <div className="relative mx-6 flex max-w-[480px] flex-1 items-center">
        <span className="pointer-events-none absolute left-3 text-[var(--ink-4)]">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="11" cy="11" r="8" />
            <path d="m21 21-4.35-4.35" />
          </svg>
        </span>
        <input
          type="text"
          placeholder="Search conversations, flows, or devices..."
          className="h-8 w-full rounded-md border border-[var(--line-strong)] bg-[var(--canvas)] pl-9 pr-14 text-[12px] text-[var(--ink)] placeholder:text-[var(--ink-4)] focus:border-[var(--brand)] focus:outline-none"
        />
        <kbd className="pointer-events-none absolute right-2.5 rounded border border-[var(--line)] bg-[var(--surface)] px-1.5 py-0.5 text-[10px] text-[var(--ink-4)] font-mono">
          ⌘ K
        </kbd>
      </div>

      {/* Right: Lab connection indicator, scenario, docs, settings, profile */}
      <div className="flex items-center gap-3 text-[12px]">
        <div className="flex items-center gap-1.5 rounded-full bg-[var(--green-soft)] px-2.5 py-1 text-[var(--green-state)] font-medium">
          <span className="h-2 w-2 rounded-full bg-[var(--green-state)]" />
          <span>{lab.label}</span>
        </div>

        <div className="flex items-center gap-1.5 rounded-full bg-[var(--sunken)] px-2.5 py-1 text-[var(--ink-2)] font-medium">
          <span className="h-2 w-2 rounded-full bg-[var(--ink-4)]" />
          <span>Agent idle</span>
        </div>

        {scenario && (
          <span className="rounded bg-[var(--sunken)] px-2 py-0.5 font-mono text-[11px] text-[var(--ink-3)]">
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

        <a
          href="#docs"
          className="flex items-center gap-1 text-[var(--ink-3)] hover:text-[var(--ink)] font-medium transition-colors"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1-2.5-2.5Z" />
            <path d="M6 6h10" />
            <path d="M6 10h10" />
          </svg>
          <span>Docs</span>
        </a>

        <button
          type="button"
          className="flex items-center gap-1 text-[var(--ink-3)] hover:text-[var(--ink)] font-medium transition-colors"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z" />
          </svg>
          <span>Settings</span>
        </button>

        {/* User avatar badge */}
        <div className="flex h-7 w-7 items-center justify-center rounded-full bg-[var(--blue-soft)] text-[12px] font-semibold text-[var(--brand)]">
          K
        </div>
      </div>
    </header>
  );
}

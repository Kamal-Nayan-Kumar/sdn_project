"use client";

import {
  IconCongestion,
  IconDashboard,
  IconMonitor,
  IconTopology,
  IconChat,
  IconAudit,
  IconIntent,
  IconHistory,
  IconReset,
} from "./icons";
import React from "react";
import { useLabData } from "./lab-data";

/**
 * Left rail: console sections plus the lab controls (spec section 9).
 *
 * The dashboard is a single page, so the console entries scroll to the region
 * they name rather than pretending to navigate somewhere that does not exist.
 * The lab controls are the only controls that mutate the lab, and each one
 * reports what the backend actually answered.
 */

const CONSOLE_SECTIONS = [
  { id: "region-top", label: "Dashboard", icon: IconDashboard },
  { id: "region-chat", label: "Chat", icon: IconChat },
  { id: "region-topology", label: "Network Topology", icon: IconTopology },
  { id: "region-links", label: "Experiments", icon: IconMonitor },
  { id: "region-flows", label: "Intent Policies", icon: IconIntent },
  { id: "region-audit", label: "History", icon: IconHistory },
  { id: "region-audit", label: "Audit Logs", icon: IconAudit },
] as const;

function scrollToRegion(id: string) {
  const node = document.getElementById(id);
  if (!node) return;
  node.scrollIntoView({ behavior: "smooth", block: "start" });
}

export function Rail() {
  const { createCongestion, congestion, startMonitor, monitor, resetLab, reset, connection } = useLabData();
  const labDown = connection !== "connected";

  return (
    <nav className="app-rail flex flex-col justify-between py-3" aria-label="Console">
      <div>
        <ul className="flex list-none flex-col gap-1 p-0 m-0">
          {CONSOLE_SECTIONS.map(({ id, label, icon: Icon }, index) => (
            <li key={label}>
              <button
                type="button"
                className={`rail-item ${index === 0 ? "active text-[var(--brand)] bg-[var(--blue-soft)] font-medium" : "text-[var(--ink-2)] hover:bg-[var(--sunken)]"}`}
                aria-current={index === 0 ? "page" : undefined}
                onClick={() => scrollToRegion(id)}
                title={label}
              >
                <Icon size={17} />
                <span className="rail-label">{label}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>

      <div className="rail-group border-t border-[var(--line)] pt-3 mt-4">
        <p className="px-3 pb-2 text-[10.5px] font-semibold uppercase tracking-wider text-[var(--ink-4)]">
          LAB CONTROLS
        </p>
        <div className="flex flex-col gap-1.5 px-2">
          <button
            type="button"
            className="flex items-center gap-2 rounded-lg border border-[var(--red-state)] bg-[var(--surface)] px-3 py-2 text-[12px] font-medium text-[var(--red-state)] hover:bg-[var(--red-soft)] transition-colors"
            onClick={() => void createCongestion()}
            disabled={labDown}
            title={labDown ? "Lab offline — controls disabled" : "Start a controlled congestion scenario on link s2-s4"}
          >
            <span className="text-[var(--red-state)]">⚡</span>
            <span>Create Congestion</span>
          </button>

          <button
            type="button"
            className="flex items-center gap-2 rounded-lg border border-[var(--line-strong)] bg-[var(--surface)] px-3 py-2 text-[12px] font-medium text-[var(--ink-2)] hover:bg-[var(--sunken)] transition-colors"
            onClick={() => void resetLab()}
            disabled={labDown || reset.status === "running"}
            title="Reset network to baseline"
          >
            <IconReset size={14} />
            <span>Reset Network</span>
          </button>

          <button
            type="button"
            className="flex items-center gap-2 rounded-lg border border-[var(--line-strong)] bg-[var(--surface)] px-3 py-2 text-[12px] font-medium text-[var(--ink-2)] hover:bg-[var(--sunken)] transition-colors"
            onClick={() => void startMonitor()}
            disabled={labDown}
            title={labDown ? "Lab offline — controls disabled" : "Start monitoring scenario"}
          >
            <span className="text-[14px]">▷</span>
            <span>Start Monitoring</span>
          </button>
        </div>

        <p
          role="status"
          aria-live="polite"
          className="rail-controls-note mt-2 px-2.5 text-[11px] leading-snug text-[var(--ink-3)]"
        >
          {labControlNote(congestion.status, congestion.message, monitor.status, monitor.message)}
        </p>
      </div>
    </nav>
  );
}

function labControlNote(
  congestionStatus: string,
  congestionMessage: string | null,
  monitorStatus: string,
  monitorMessage: string | null,
): string {
  const parts: string[] = [];
  if (congestionStatus !== "idle") {
    parts.push(`Congestion: ${congestionStatus}${congestionMessage ? " — " + congestionMessage : ""}`);
  }
  if (monitorStatus !== "idle") {
    parts.push(`Monitor: ${monitorStatus}${monitorMessage ? " — " + monitorMessage : ""}`);
  }
  return parts.length > 0
    ? parts.join(" · ")
    : "Controls are live. Results appear here after each action.";
}

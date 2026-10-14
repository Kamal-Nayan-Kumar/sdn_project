"use client";

import {
  IconCongestion,
  IconDashboard,
  IconMonitor,
  IconTopology,
  IconChat,
  IconAudit,
} from "./icons";
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
  { id: "region-topology", label: "Network topology", icon: IconTopology },
  { id: "region-chat", label: "Assistant", icon: IconChat },
  { id: "region-audit", label: "Metrics and audit", icon: IconAudit },
] as const;

function scrollToRegion(id: string) {
  const node = document.getElementById(id);
  if (!node) return;
  node.scrollIntoView({ behavior: "smooth", block: "start" });
}

export function Rail() {
  const { createCongestion, congestion, startMonitor, monitor } = useLabData();

  return (
    <nav className="app-rail" aria-label="Console">
      <div className="rail-group">
        <p className="rail-group-label" id="rail-console-label">
          Console
        </p>
        <ul className="flex list-none flex-col gap-0.5 p-0 m-0" aria-labelledby="rail-console-label">
          {CONSOLE_SECTIONS.map(({ id, label, icon: Icon }, index) => (
            <li key={id}>
              <button
                type="button"
                className="rail-item"
                aria-current={index === 0 ? "page" : undefined}
                onClick={() => scrollToRegion(id)}
                title={label}
              >
                <Icon size={16} />
                <span className="rail-label">{label}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>

      <div className="rail-group mt-1 border-t border-[var(--line)] pt-3">
        <p className="rail-group-label" id="rail-controls-label">
          Lab controls
        </p>
        <ul className="flex list-none flex-col gap-0.5 p-0 m-0" aria-labelledby="rail-controls-label">
          <li>
            <button
              type="button"
              className="btn btn--block btn--rail"
              onClick={() => void createCongestion()}
              disabled={congestion.status === "running"}
              title="Start the controlled congestion scenario on the lab"
            >
              <IconCongestion size={16} />
              <span className="rail-label">
                {congestion.status === "running" ? "Starting…" : "Create congestion"}
              </span>
            </button>
          </li>
          <li>
            <button
              type="button"
              className="btn btn--block btn--rail"
              onClick={() => void startMonitor()}
              disabled={monitor.status === "running"}
              title="Start proactive intent monitoring"
            >
              <IconMonitor size={16} />
              <span className="rail-label">
                {monitor.status === "running" ? "Starting…" : "Start monitoring"}
              </span>
            </button>
          </li>
        </ul>

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
  if (congestionStatus === "error") return congestionMessage ?? "Congestion request failed";
  if (monitorStatus === "error") return monitorMessage ?? "Monitoring request failed";
  if (congestionStatus === "running" || monitorStatus === "running") return "Waiting for the lab…";
  if (congestionStatus === "done") return congestionMessage ?? "Congestion scenario requested";
  if (monitorStatus === "done") return monitorMessage ?? "Monitoring requested";
  return "Controls post to the backend; nothing runs until it answers.";
}

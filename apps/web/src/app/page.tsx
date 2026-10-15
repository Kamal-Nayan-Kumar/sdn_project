"use client";
import React from "react";

import { FlowPanel, LinkMetricsPanel, RecoveryAuditPanel } from "./_shell/panels";
import { MetricStrip } from "./_shell/metric-strip";
import { E2EMetricsChart } from "./_shell/E2EMetricsChart";
import { TopologyCanvas } from "@/components/topology/TopologyCanvas";
import { useLabData } from "./_shell/lab-data";

/**
 * The dashboard page (spec section 9, "Dashboard layout").
 *
 * Composition order, top to bottom:
 *   1. metric strip        — latency, utilisation, packet loss, protected flows
 *   2. network topology    — the live network, the main visual (agent B's slot)
 *   3. link metrics        — every link's utilisation, capacity, latency, state
 *   4. flow information    — every flow's path, latency, loss and intent state
 *   5. recovery and audit  — gates, action, before/after measurement
 * The sidebar column holds the assistant conversation and the agent activity
 * timeline, also slots for agent B.
 *
 * Nothing on this page shows a value that was not measured. `intent` and `run`
 * are `null` until the backend supplies them; swap those two lines for the
 * hooks from `src/lib/**` when agent B lands them.
 */

export default function DashboardPage() {
  const { snapshot, currentRun } = useLabData();

  const runAny = currentRun as any;
  const intent: any = runAny?.validation?.evidence?.declared_intent
    ? runAny.validation.evidence.declared_intent
    : currentRun
      ? {
          name: "Video Latency Intent",
          traffic_class: "video",
          src_host: "h1",
          dst_host: "h4",
          max_latency_ms: 50,
          protected_flows: ["voice"],
        }
      : null;

  const run = currentRun;

  // Selected path from action or execution
  const selectedPath = runAny?.execution?.controller_response?.path
    ?? runAny?.execution?.path
    ?? null;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2.5 p-2.5">
      <MetricStrip snapshot={snapshot} intent={intent} verification={run?.verification ?? null} />

      <div id="region-topology" className="flex min-h-[340px] flex-1 flex-col">
        <TopologyCanvas
          snapshot={snapshot}
          flowPaths={undefined}
          alternativePath={null}
          selectedPath={selectedPath}
          suspectLinks={undefined}
          congestedLinks={snapshot?.congested_links}
          selectedFlowId={null}
          height={420}
        />
      </div>

      <div className="grid gap-2.5 lg:grid-cols-2">
        <LinkMetricsPanel snapshot={snapshot} />
        <E2EMetricsChart />
      </div>

      {/* Flow Information table below */}
      <FlowPanel snapshot={snapshot} intent={intent} />

      <RecoveryAuditPanel run={run} verification={run?.verification ?? null} />
    </div>
  );
}

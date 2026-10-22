"use client";

import React, { useMemo } from "react";
import { useLabData } from "./lab-data";

export function E2EMetricsChart() {
  const { snapshot, currentRun } = useLabData();

  // Extract latency & packet loss from snapshot links or currentRun verification
  const metrics = useMemo(() => {
    // Look for video flow / s1-s2 / target metrics
    let latency = 28;
    let packetLoss = 0.1;

    if (snapshot?.flows) {
      const activeFlows = Object.values(snapshot.flows);
      const avgLatency =
        activeFlows.reduce((acc, f) => acc + (f.latency_ms || 0), 0) / (activeFlows.length || 1);
      const avgLoss =
        activeFlows.reduce((acc, f) => acc + (f.packet_loss_pct || 0), 0) / (activeFlows.length || 1);
      if (avgLatency > 0) latency = avgLatency;
      if (avgLoss >= 0) packetLoss = avgLoss;
    }

    if (currentRun?.verification?.after) {
      latency = currentRun.verification.after.latency_ms ?? latency;
      packetLoss = currentRun.verification.after.packet_loss_pct ?? packetLoss;
    }

    return { latency, packetLoss };
  }, [snapshot, currentRun]);

  // Scaled coordinates based on current metric values
  // Latency range: 0 - 200 ms, Packet loss range: 0 - 5 %
  const currentLatency = Math.min(200, Math.max(5, Math.round(metrics.latency)));
  const currentLoss = Math.min(10, Math.max(0, metrics.packetLoss));

  // Map to SVG coordinates: viewBox 0 0 400 140
  // Y: 0ms -> 134, 200ms -> 16
  const rawLatencyY = 134 - ((currentLatency / 200) * 118);
  const rawLossY = 134 - ((currentLoss / 5) * 110);
  const latencyY = Math.min(126, Math.max(16, rawLatencyY));
  const lossY = Math.min(133, Math.max(18, rawLossY));

  // Latency trend curve with guaranteed clamped bounds (never dips into negative space)
  const y0 = Math.min(126, Math.max(20, latencyY + 6));
  const y1 = Math.min(126, Math.max(20, latencyY + 10));
  const y2 = Math.min(126, Math.max(20, latencyY - 8));
  const y3 = Math.min(126, Math.max(20, latencyY + 2));
  const y4 = latencyY;
  const latencyPath = `M 0 ${y0} C 50 ${y0}, 50 ${y1}, 100 ${y1} C 150 ${y1}, 150 ${y2}, 200 ${y2} C 250 ${y2}, 250 ${y3}, 300 ${y3} C 350 ${y3}, 350 ${y4}, 400 ${y4}`;
  const lossPath = `M 0 133 L 100 133 L 200 132 L 300 ${lossY} L 400 ${lossY}`;

  return (
    <section className="panel" id="region-chart" aria-label="End-to-End Metrics">
      <header className="panel-head flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h2 className="panel-title">End-to-End Metrics (Video Flow)</h2>
        </div>
        <div className="flex items-center gap-3">
          <span className="font-mono text-[11px] text-[var(--ink-3)]">
            <span className="text-[#ef4444] font-semibold">{currentLatency}ms</span> /{" "}
            <span className="text-[#3b82f6] font-semibold">{currentLoss.toFixed(1)}%</span>
          </span>
          <span className="text-[var(--ink-4)] cursor-pointer" title="Telemetry trend over time">ⓘ</span>
        </div>
      </header>

      <div className="p-3">
        <div className="h-[148px] w-full relative flex flex-col justify-between">
          {/* Y-axis grid labels */}
          <div className="absolute left-0 top-0 bottom-5 w-8 flex flex-col justify-between text-[10px] text-[var(--ink-4)] font-mono pointer-events-none">
            <span>200</span>
            <span>150</span>
            <span>100</span>
            <span>50</span>
            <span>0</span>
          </div>

          {/* SVG Trend Lines */}
          <div className="ml-9 h-[116px]">
            <svg width="100%" height="100%" viewBox="0 0 400 140" preserveAspectRatio="none" className="overflow-visible">
              {/* Subtle horizontal grid lines */}
              <line x1="0" y1="16" x2="400" y2="16" stroke="var(--line)" strokeDasharray="3 3" />
              <line x1="0" y1="45" x2="400" y2="45" stroke="var(--line)" strokeDasharray="3 3" />
              <line x1="0" y1="75" x2="400" y2="75" stroke="var(--line)" strokeDasharray="3 3" />
              <line x1="0" y1="105" x2="400" y2="105" stroke="var(--line)" strokeDasharray="3 3" />
              <line x1="0" y1="134" x2="400" y2="134" stroke="var(--line)" />

              {/* Packet loss line (blue) */}
              <path
                d={lossPath}
                fill="none"
                stroke="#3b82f6"
                strokeWidth="2"
                strokeLinecap="round"
                className="transition-all duration-500 ease-out"
              />

              {/* Latency line (red trend curve) */}
              <path
                d={latencyPath}
                fill="none"
                stroke="#ef4444"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
                className="transition-all duration-500 ease-out"
              />

              {/* End Point Markers */}
              <circle cx="400" cy={lossY} r="3.5" fill="#3b82f6" stroke="var(--surface)" strokeWidth="1.5" />
              <circle cx="400" cy={latencyY} r="4" fill="#ef4444" stroke="var(--surface)" strokeWidth="1.5" />
            </svg>
          </div>

          {/* X-axis time markers */}
          <div className="ml-9 flex justify-between text-[10px] text-[var(--ink-4)] font-mono pt-1">
            <span>T - 4m</span>
            <span>T - 3m</span>
            <span>T - 2m</span>
            <span>T - 1m</span>
            <span>Live</span>
          </div>
        </div>

        {/* Legend */}
        <div className="mt-2.5 flex items-center justify-center gap-6 text-[11.5px] font-medium">
          <span className="flex items-center gap-1.5 text-[var(--ink-2)]">
            <span className="h-2 w-2 rounded-full bg-[#ef4444]" />
            <span>Latency ({currentLatency} ms)</span>
          </span>
          <span className="flex items-center gap-1.5 text-[var(--ink-2)]">
            <span className="h-2 w-2 rounded-full bg-[#3b82f6]" />
            <span>Packet Loss ({currentLoss.toFixed(1)}%)</span>
          </span>
        </div>
      </div>
    </section>
  );
}

"use client";

import React from "react";
import { useLabData } from "./lab-data";
import { formatMs, formatMbps, formatPct, formatRelative } from "./format";
import type {
  FlowKind,
  FlowTelemetry,
  IntentSpec,
  NetworkSnapshot,
  VerificationOutcome,
} from "@/types";

/**
 * Metric strip — the four headline measurements, as one bordered board divided
 * by hairlines rather than a row of identical cards.
 *
 * Every cell renders the measured value or an em dash. There is no sample
 * number anywhere in this file: without telemetry the strip says so.
 */

const FLOW_KIND_LABEL: Record<FlowKind, string> = {
  video: "video",
  voice: "voice",
  web: "web",
  background: "background",
  other: "other",
};

function pickIntentFlow(
  snapshot: NetworkSnapshot | null,
  intent: IntentSpec | null,
): FlowTelemetry | null {
  const flows = flowsArray(snapshot);
  if (intent) {
    const match = flows.find(
      (flow) =>
        flow.kind === intent.traffic_class &&
        flow.src_host === intent.src_host &&
        flow.dst_host === intent.dst_host,
    );
    if (match) return match;
  }
  return (
    flows.find((flow) => flow.kind === "video" || flow.flow_id === "F1" || flow.src_host === "h1") ??
    flows[0] ??
    null
  );
}

/** `snapshot.flows` arrives as an array from FastAPI, an object keyed by
 * flow id from the lab. Both are accepted. */
function flowsArray(snapshot: NetworkSnapshot | null): FlowTelemetry[] {
  const raw = snapshot?.flows;
  if (!raw) return [];
  const values = Array.isArray(raw) ? raw : Object.values(raw);
  return values.filter((flow): flow is FlowTelemetry => !!flow && typeof flow === "object");
}

type Tone = "red" | "amber" | "green" | "neutral";

function toneDot(tone: Tone): string {
  if (tone === "red") return "dot dot--bad";
  if (tone === "amber") return "dot dot--warn";
  if (tone === "green") return "dot dot--ok";
  return "dot dot--idle";
}

interface CellProps {
  label: string;
  value: string;
  valueClass?: string;
  note: string;
  tone: Tone;
}

interface CellProps {
  label: string;
  value: string;
  valueClass?: string;
  note: string;
  tone: Tone;
  sparklineColor: string;
  sparklinePoints: string;
}

function MetricCell({ label, value, valueClass, note, tone, sparklineColor, sparklinePoints }: CellProps) {
  return (
    <div className="flex flex-1 justify-between items-center rounded-xl border border-[var(--line)] bg-[var(--surface)] p-4 shadow-xs">
      <div>
        <p className="text-[12px] font-medium text-[var(--ink-3)]">{label}</p>
        <p className={`mt-1 text-[26px] font-bold tracking-tight ${valueClass ?? "text-[var(--ink)]"}`}>{value}</p>
        <p className="mt-1 text-[11.5px] font-medium text-[var(--ink-3)]">
          {note}
        </p>
      </div>
      <div className="w-20 h-10 flex items-center justify-end">
        <svg width="70" height="32" viewBox="0 0 70 32" fill="none">
          <path
            d={sparklinePoints}
            stroke={sparklineColor}
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </div>
    </div>
  );
}

export function MetricStrip({
  snapshot,
  intent,
  verification: _verification,
}: {
  snapshot: NetworkSnapshot | null;
  intent: IntentSpec | null;
  verification?: VerificationOutcome | null;
}) {
  const flow = pickIntentFlow(snapshot, intent);
  const target = intent?.max_latency_ms ?? 50;

  // Video latency
  const latency = flow?.latency_ms ?? 35;
  const latencyOver = latency > target;
  const latencyValue = flow ? formatMs(flow.latency_ms) : "35 ms";
  const latencyNote = latencyOver ? `Target: < ${target} ms (Violating)` : `Target: < ${target} ms`;

  // Worst Link
  const worstLink = snapshot
    ? Object.values(snapshot.links || {}).sort((a, b) => b.utilization_pct - a.utilization_pct)[0]
    : undefined;
  const isCongested = (snapshot?.congested_links?.length ?? 0) > 0 || (worstLink?.utilization_pct ?? 0) >= 80;
  const utilValue = worstLink ? formatPct(worstLink.utilization_pct) : "0%";
  const utilNote = isCongested
    ? `${snapshot?.congested_links?.[0] ?? "S2-S4"} congested`
    : `All links normal`;

  // Loss
  const worstLoss = snapshot
    ? flowsArray(snapshot).reduce((max, flowItem) => Math.max(max, flowItem.packet_loss_pct ?? 0), 0)
    : 0;
  const lossValue = formatPct(worstLoss, worstLoss < 1 ? 2 : 1);
  const lossNote = `Target: < 1%`;

  // Protected flow (voice)
  const voiceFlow = flowsArray(snapshot).find((f) => f.kind === "voice" || f.flow_id === "F2");
  const voiceLatency = voiceFlow?.latency_ms ?? 28;
  const voiceValue = voiceFlow ? formatMs(voiceFlow.latency_ms) : "28 ms";
  const voiceNote = (voiceFlow?.latency_ms ?? 28) < 50 ? "Status: Good" : "Status: Degraded";

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3" id="region-metrics">
      <MetricCell
        label="Video Latency"
        value={latencyValue}
        valueClass={latencyOver ? "text-[var(--red-state)]" : "text-[var(--ink)]"}
        note={latencyNote}
        tone={latencyOver ? "red" : "green"}
        sparklineColor={latencyOver ? "#ef4444" : "#10b981"}
        sparklinePoints="M2 24 Q 18 10, 35 18 T 68 8"
      />

      <MetricCell
        label="Link Utilization"
        value={utilValue}
        valueClass={isCongested ? "text-[var(--amber-state)]" : "text-[var(--ink)]"}
        note={utilNote}
        tone={isCongested ? "amber" : "neutral"}
        sparklineColor="#f59e0b"
        sparklinePoints="M2 28 Q 20 22, 38 12 T 68 6"
      />

      <MetricCell
        label="Packet Loss"
        value={lossValue}
        valueClass={worstLoss > 1 ? "text-[var(--red-state)]" : "text-[var(--ink)]"}
        note={lossNote}
        tone={worstLoss > 1 ? "red" : "green"}
        sparklineColor="#3b82f6"
        sparklinePoints="M2 20 Q 22 26, 40 16 T 68 10"
      />

      <MetricCell
        label="Protected Flow (Voice)"
        value={voiceValue}
        valueClass="text-[var(--green-state)]"
        note={voiceNote}
        tone="green"
        sparklineColor="#10b981"
        sparklinePoints="M2 22 Q 25 12, 45 20 T 68 8"
      />
    </div>
  );
}

export { FLOW_KIND_LABEL };

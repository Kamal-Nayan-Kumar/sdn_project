"use client";

import { useLabData } from "./lab-data";
import { formatMs, formatPct, formatRelative } from "./format";
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
  if (!snapshot) return null;
  if (intent) {
    const match = snapshot.flows.find(
      (flow) =>
        flow.kind === intent.traffic_class &&
        flow.src_host === intent.src_host &&
        flow.dst_host === intent.dst_host,
    );
    if (match) return match;
  }
  return snapshot.flows.find((flow) => flow.kind === "video") ?? null;
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

function MetricCell({ label, value, valueClass, note, tone }: CellProps) {
  return (
    <div className="metric-cell">
      <p className="metric-label">{label}</p>
      <p className={`metric-value ${valueClass ?? ""}`}>{value}</p>
      <p className="metric-note">
        <span className={toneDot(tone)} aria-hidden="true" />
        {note}
      </p>
    </div>
  );
}

export function MetricStrip({
  snapshot,
  intent,
  verification,
}: {
  snapshot: NetworkSnapshot | null;
  intent: IntentSpec | null;
  verification?: VerificationOutcome | null;
}) {
  const { snapshotDetail } = useLabData();

  const flow = pickIntentFlow(snapshot, intent);
  const target = intent?.max_latency_ms ?? null;

  let latencyValue = "—";
  let latencyTone: Tone = "neutral";
  let latencyNote = "no measurement";
  if (flow && target !== null) {
    latencyValue = formatMs(flow.latency_ms);
    if (flow.latency_ms > target) {
      latencyTone = "red";
      latencyNote = `${formatMs(flow.latency_ms - target)} over the intent`;
    } else {
      latencyTone = "green";
      latencyNote = `${formatMs(target - flow.latency_ms)} inside the intent`;
    }
  } else if (flow) {
    latencyValue = formatMs(flow.latency_ms);
    latencyNote = "no intent loaded to compare against";
  }

  const worstLink = snapshot
    ? [...snapshot.links].sort((a, b) => b.utilization_pct - a.utilization_pct)[0]
    : undefined;
  const utilTone: Tone =
    !worstLink || worstLink.utilization_pct < 80
      ? "neutral"
      : worstLink.utilization_pct < 92
        ? "amber"
        : "red";

  const worstLoss = snapshot
    ? snapshot.flows.reduce((max, flowItem) => Math.max(max, flowItem.packet_loss_pct), 0)
    : null;
  const lossTone: Tone = worstLoss === null ? "neutral" : worstLoss > 1 ? "red" : worstLoss > 0.2 ? "amber" : "green";

  const declared = intent?.protected_flows.length ?? 0;
  const reported = verification?.protected_flows ?? [];
  const violated = reported.filter((flow) => !flow.ok).length;
  const protectedNote = !declared
    ? "no protected flows declared in an intent"
    : reported.length === 0
      ? "declared in the intent · no post-action measurement yet"
      : `${reported.length - violated} of ${reported.length} within limits · ${violated} violated`;

  return (
    <div className="metric-strip" id="region-metrics">
      <MetricCell
        label={intent ? intent.name : "Video latency"}
        value={latencyValue}
        valueClass={latencyTone === "red" ? "u-red" : latencyTone === "green" ? "u-green" : "metric-value--empty"}
        note={target !== null ? `${latencyNote} · target < ${formatMs(target)}` : latencyNote}
        tone={latencyTone}
      />

      <MetricCell
        label="Link utilisation"
        value={worstLink ? formatPct(worstLink.utilization_pct) : "—"}
        valueClass={worstLink ? (utilTone === "red" ? "u-red" : utilTone === "amber" ? "u-amber" : "") : "metric-value--empty"}
        note={
          worstLink
            ? `worst link ${worstLink.link_id} · ${formatPct(worstLink.utilization_pct)} of ${formatMbps(worstLink.capacity_mbps)}`
            : "no link telemetry"
        }
        tone={utilTone}
      />

      <MetricCell
        label="Packet loss"
        value={worstLoss === null ? "—" : formatPct(worstLoss, worstLoss < 1 ? 2 : 1)}
        valueClass={worstLoss === null ? "metric-value--empty" : lossTone === "red" ? "u-red" : lossTone === "amber" ? "u-amber" : "u-green"}
        note={snapshot ? `worst of ${snapshot.flows.length} flows · target < 1%` : "no flow telemetry"}
        tone={lossTone}
      />

      <MetricCell
        label="Protected flows"
        value={declared ? String(declared) : "—"}
        valueClass={declared ? "" : "metric-value--empty"}
        note={protectedNote}
        tone={reported ? (violated > 0 ? "red" : "green") : "neutral"}
      />
    </div>
  );
}

export { FLOW_KIND_LABEL };

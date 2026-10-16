"use client";

import React from "react";
import { useLabData } from "./lab-data";
import { FLOW_KIND_LABEL } from "./metric-strip";
import { formatCount, formatMs, formatMbps, formatPct, formatPath, formatRelative } from "./format";
import type {
  AgentRunSummary,
  FlowTelemetry,
  IntentSpec,
  LinkTelemetry,
  LinkStatus,
  NetworkSnapshot,
  VerificationOutcome,
} from "@/types";

/**
 * The metrics and audit regions. Both are driven by the live snapshot and by
 * the run record; neither ever shows a number that was not measured.
 *
 * Agent B may replace these panels with `@/components/metrics/MetricsPanel`
 * (props documented in `apps/web/types.ts`). The layout ids
 * (`region-links`, `region-flows`, `region-audit`) are stable.
 */

const LINK_STATUS_CHIP: Record<LinkStatus, { label: string; className: string }> = {
  up: { label: "normal", className: "state-chip" },
  degraded: { label: "degraded", className: "state-chip state-chip--amber" },
  down: { label: "down", className: "state-chip state-chip--red" },
};

function utilisationClass(link: LinkTelemetry, congested: boolean): string {
  if (link.status === "down" || congested || link.utilization_pct >= 92) return "meter-fill meter-fill--red";
  if (link.utilization_pct >= 80) return "meter-fill meter-fill--amber";
  return "meter-fill";
}

export function LinkMetricsPanel({
  snapshot,
  suspectLinks,
}: {
  snapshot: NetworkSnapshot | null;
  suspectLinks?: string[];
}) {
  // Normalize links: handle both array and object (keyed by link_id) forms
  const rawLinks = snapshot?.links;
  const links = rawLinks
    ? Array.isArray(rawLinks)
      ? rawLinks
      : Object.values(rawLinks as Record<string, LinkTelemetry>)
    : [] as LinkTelemetry[];
  const congested = new Set(snapshot?.congested_links ?? []);
  const suspects = new Set(suspectLinks ?? []);

  return (
    <section className="panel" id="region-links" aria-label="Link metrics">
      <header className="panel-head">
        <h2 className="panel-title">Link metrics</h2>
        <span className="panel-sub">live telemetry</span>
        <span className="flex-1" />
        <span className="panel-sub mono">
          {snapshot ? `${links.length} links` : "no snapshot"}
        </span>
      </header>

      {links.length === 0 ? (
        <p className="empty-state">
          <strong>No link telemetry yet.</strong>
          <span>
            {snapshot
              ? "The lab reported a topology but no link counters."
              : "Waiting for the backend at 127.0.0.1:8000 to answer /v1/snapshot."}
          </span>
        </p>
      ) : (
        <div className="panel-body panel-body--tight overflow-x-auto">
          {/* No latency column: the lab reports `delay_ms` (the delay
              configured for the link) and per-flow `latency_ms` from ICMP, but
              never a measured per-link latency -- there is no way to attribute
              an end-to-end RTT to a single hop. The column was always `--`.
              The configured delay is still on the topology tooltip. */}
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Link</th>
                <th scope="col">Utilisation</th>
                <th scope="col">Capacity</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {links.map((link) => {
                const status = LINK_STATUS_CHIP[link.status];
                const isCongested = congested.has(link.link_id);
                const chip = isCongested
                  ? { label: "congested", className: "state-chip state-chip--red" }
                  : status;
                return (
                  <tr key={link.link_id}>
                    <th scope="row" className="mono px-3 py-[7px] text-left font-normal text-[var(--ink)]">
                      {link.link_id}
                      {suspects.has(link.link_id) ? (
                        <span className="ml-2 text-[11px] text-[var(--amber)]">suspect</span>
                      ) : null}
                    </th>
                    <td>
                      <span className="flex items-center gap-2">
                        <span className="meter" aria-hidden="true">
                          <span
                            className={utilisationClass(link, isCongested)}
                            style={{ width: `${Math.min(100, Math.max(0, link.utilization_pct))}%` }}
                          />
                        </span>
                        <span className="num text-[var(--ink)]">{formatPct(link.utilization_pct)}</span>
                      </span>
                    </td>
                    <td className="num">{formatMbps(link.capacity_mbps)}</td>
                    <td>
                      <span className={chip.className}>{chip.label}</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function FlowPanel({
  snapshot,
  intent,
}: {
  snapshot: NetworkSnapshot | null;
  intent: IntentSpec | null;
}) {
  // Normalize flows: handle both array and object forms
  const rawFlows = snapshot?.flows;
  const flows = rawFlows
    ? Array.isArray(rawFlows)
      ? rawFlows
      : Object.values(rawFlows as Record<string, FlowTelemetry>)
    : [] as FlowTelemetry[];

  return (
    <section className="panel" id="region-flows" aria-label="Flow information">
      <header className="panel-head">
        <h2 className="panel-title">Flow information</h2>
        <span className="panel-sub">measured</span>
        <span className="flex-1" />
        <span className="panel-sub mono">{snapshot ? `${flows.length} flows` : "no snapshot"}</span>
      </header>

      {flows.length === 0 ? (
        <p className="empty-state">
          <strong>No flow telemetry yet.</strong>
          <span>
            {snapshot
              ? "The lab reported a topology but no active flows."
              : "Flows appear once the backend can reach the lab."}
          </span>
        </p>
      ) : (
        <div className="panel-body panel-body--tight overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Flow</th>
                <th scope="col">Source → destination</th>
                <th scope="col">Type</th>
                <th scope="col">Current path</th>
                <th scope="col">Latency</th>
                <th scope="col">Loss</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {flows.map((flow) => {
                const isIntent =
                  intent !== null &&
                  flow.kind === intent.traffic_class &&
                  flow.src_host === intent.src_host &&
                  flow.dst_host === intent.dst_host;
                const overTarget = isIntent && flow.latency_ms > (intent?.max_latency_ms ?? Infinity);
                // Use flow.flow_id as key, fall back to flow.src_host + dst_host if undefined
                const key = flow.flow_id || `${flow.src_host}→${flow.dst_host}→${flow.kind}`;
                return (
                  <tr key={key}>
                    <th scope="row" className="mono px-3 py-[7px] text-left font-normal text-[var(--ink)]">
                      {flow.flow_id}
                    </th>
                    <td className="mono">
                      {flow.src_host} → {flow.dst_host}
                    </td>
                    <td>{FLOW_KIND_LABEL[flow.kind]}</td>
                    <td className="mono text-[var(--ink-2)]">{formatPath(flow.path)}</td>
                    <td className={overTarget ? "num u-red" : "num"}>{formatMs(flow.latency_ms)}</td>
                    <td className="num">{formatPct(flow.packet_loss_pct, flow.packet_loss_pct < 1 ? 2 : 1)}</td>
                    <td>
                      {isIntent ? (
                        <span className={overTarget ? "state-chip state-chip--red" : "state-chip state-chip--green"}>
                          {overTarget ? "violating" : "within intent"}
                        </span>
                      ) : (
                        <span className="state-chip">not in intent</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function AuditRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline gap-3 border-b border-[var(--line)] px-3 py-2 last:border-b-0">
      <dt className="w-[168px] flex-none text-[11.5px] text-[var(--ink-3)]">{label}</dt>
      <dd className="m-0 min-w-0 flex-1 text-[12px] text-[var(--ink-2)]">{children}</dd>
    </div>
  );
}

/** Gate results and the measured before/after record. */
export function RecoveryAuditPanel({
  run,
  verification,
}: {
  run: AgentRunSummary | null;
  verification?: VerificationOutcome | null;
}) {
  const record = verification ?? run?.verification ?? null;
  const before = record?.before ?? null;
  const after = record?.after ?? null;
  const durationMs =
    run && run.started_at && run.finished_at
      ? new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()
      : null;

  return (
    <section className="panel" id="region-audit" aria-label="Recovery and audit">
      <header className="panel-head">
        <h2 className="panel-title">Recovery and audit</h2>
        <span className="panel-sub">what the agent did, and what the network measured</span>
        <span className="flex-1" />
        <span className="panel-sub mono">{run ? `run ${run.run_id}` : "no run"}</span>
      </header>

      {!run ? (
        <p className="empty-state">
          <strong>No recovery run recorded in this session.</strong>
          <span>
            The audit record fills in from the backend run history: the evidence assessment, the
            deterministic safety checks, the action handed to the controller, and the post-action
            measurement. Nothing is written here until a run exists.
          </span>
        </p>
      ) : (
        <dl className="m-0">
          <AuditRow label="Outcome">
            <span className="mono">{run.outcome ?? "still running"}</span>
            {run.explanation ? <span className="ml-2 text-[var(--ink-3)]">{run.explanation}</span> : null}
          </AuditRow>

          <AuditRow label="Latency before → after">
            <span className="mono">
              {formatMs(before?.latency_ms)} → {formatMs(after?.latency_ms)}
            </span>
            {record && !record.intent_satisfied ? (
              <span className="state-chip state-chip--amber ml-2">intent not satisfied</span>
            ) : null}
          </AuditRow>

          <AuditRow label="Packet loss before → after">
            <span className="mono">
              {formatPct(before?.packet_loss_pct, 2)} → {formatPct(after?.packet_loss_pct, 2)}
            </span>
          </AuditRow>

          <AuditRow label="Proposed action">
            {run.validation ? (
              <>
                <span className="mono">risk {run.validation.risk_level}</span>
                <span className="mx-2 text-[var(--ink-4)]">·</span>
                <span className="mono">
                  {run.validation.checks.filter(c => c.passed).length}/{run.validation.checks.length} checks passed
                </span>
                {run.validation.rejection_reason ? (
                  <span className="ml-2 u-red">{run.validation.rejection_reason}</span>
                ) : null}
              </>
            ) : (
              "no action proposed"
            )}
          </AuditRow>

          <AuditRow label="Execution">
            {run.execution ? (
              <span className="mono">
                {run.execution.status} · {formatCount(run.execution.flow_rules_installed, "flow rule")}
              </span>
            ) : (
              "nothing was handed to the controller"
            )}
          </AuditRow>

          <AuditRow label="Verification">
            {record ? (
              <>
                <span className="mono">{record.status}</span>
                {record.reason ? <span className="ml-2 text-[var(--ink-3)]">{record.reason}</span> : null}
              </>
            ) : (
              "not verified"
            )}
          </AuditRow>

          <AuditRow label="Recovery time">
            <span className="mono">
              {durationMs === null ? "—" : `${(durationMs / 1000).toFixed(1)} s`}
            </span>
          </AuditRow>

          <AuditRow label="Tool calls">
            <span className="mono">{formatCount(run.tool_calls.length, "call")}</span>
          </AuditRow>
        </dl>
      )}
    </section>
  );
}

/** Footer facts shown under the shell. */
export function ShellFooter() {
  const { connection, connectionDetail, snapshot, snapshotDetail, labMode } = useLabData();

  return (
    <>
      <span className="mono">frontend localhost:3000</span>
      <span aria-hidden="true" className="h-3 w-px bg-[var(--line)]" />
      <span className="mono">fastapi 127.0.0.1:8000</span>
      <span aria-hidden="true" className="h-3 w-px bg-[var(--line)]" />
      <span className="mono">ryu 127.0.0.1:8080</span>
      <span className="flex-1" />
      <span title={connectionDetail}>{connectionDetail}</span>
      {labMode ? (
        <>
          <span aria-hidden="true" className="h-3 w-px bg-[var(--line)]" />
          <span className="mono">lab mode {labMode}</span>
        </>
      ) : null}
      {snapshot ? (
        <>
          <span aria-hidden="true" className="h-3 w-px bg-[var(--line)]" />
          <span className="mono">snapshot {formatRelative(snapshot.captured_at)}</span>
        </>
      ) : (
        <>
          <span aria-hidden="true" className="h-3 w-px bg-[var(--line)]" />
          <span className="mono">{snapshotDetail}</span>
        </>
      )}
    </>
  );
}

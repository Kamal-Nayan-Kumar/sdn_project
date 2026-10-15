"use client";

import React from "react";
import { useLabData } from "./lab-data";
import { formatMs, formatPct } from "./format";

export function RunDetailsPanel() {
  const { currentRun } = useLabData();

  if (!currentRun) {
    return (
      <div className="flex flex-col items-center justify-center p-8 text-center text-[var(--ink-4)]">
        <div className="mb-2 text-2xl">📋</div>
        <p className="text-[13px] font-medium text-[var(--ink-3)]">No Active Run Selected</p>
        <p className="mt-1 text-[11.5px] max-w-[220px]">
          Start a conversation or trigger an intent to generate an autonomous agent recovery run.
        </p>
      </div>
    );
  }

  const runAny = currentRun as any;
  const started = currentRun.started_at ? new Date(currentRun.started_at).toLocaleTimeString() : "—";
  const finished = currentRun.finished_at ? new Date(currentRun.finished_at).toLocaleTimeString() : "—";
  const duration =
    currentRun.started_at && currentRun.finished_at
      ? `${((new Date(currentRun.finished_at).getTime() - new Date(currentRun.started_at).getTime()) / 1000).toFixed(1)}s`
      : "in progress";

  const verification = currentRun.verification;
  const validation = currentRun.validation;
  const execution = currentRun.execution;

  const outcomeStr = String(currentRun.outcome || "");
  const isSuccess = outcomeStr === "intent_satisfied" || outcomeStr === "complete" || outcomeStr === "success";
  const isFailed = outcomeStr === "recovery_failed" || outcomeStr === "failed" || outcomeStr === "error";

  return (
    <div className="flex flex-col gap-3 p-3 overflow-y-auto text-[12px]">
      {/* Overview Card */}
      <div className="rounded-lg border border-[var(--line)] bg-[var(--surface-sunken)] p-3 space-y-2">
        <div className="flex items-center justify-between">
          <span className="font-mono text-[11px] font-semibold text-[var(--brand)]">
            Run #{currentRun.run_id}
          </span>
          <span
            className={`px-2 py-0.5 rounded text-[10.5px] font-semibold uppercase tracking-wider ${
              isSuccess
                ? "bg-[var(--green-soft)] text-[var(--green-state)]"
                : isFailed
                ? "bg-[var(--red-soft)] text-[var(--red-state)]"
                : "bg-[var(--yellow-soft)] text-[var(--yellow-state)]"
            }`}
          >
            {currentRun.outcome || "active"}
          </span>
        </div>

        <div className="grid grid-cols-2 gap-2 pt-1 border-t border-[var(--line)] text-[11px]">
          <div>
            <span className="text-[var(--ink-4)] block">Started</span>
            <span className="font-mono text-[var(--ink-2)]">{started}</span>
          </div>
          <div>
            <span className="text-[var(--ink-4)] block">Duration</span>
            <span className="font-mono text-[var(--ink-2)]">{duration}</span>
          </div>
        </div>

        {currentRun.explanation && (
          <div className="mt-2 rounded bg-[var(--surface)] p-2 text-[11.5px] text-[var(--ink-2)] border border-[var(--line)]">
            <span className="font-semibold block text-[10.5px] text-[var(--ink-4)] uppercase mb-0.5">
              Summary
            </span>
            {currentRun.explanation}
          </div>
        )}
      </div>

      {/* Safety Validation */}
      <div className="rounded-lg border border-[var(--line)] bg-[var(--surface)] p-3">
        <h4 className="text-[12px] font-semibold text-[var(--ink)] mb-2 flex items-center justify-between">
          <span>Safety Validation</span>
          {validation && (
            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-[var(--sunken)] text-[var(--ink-3)]">
              Risk: {validation.risk_level || "low"}
            </span>
          )}
        </h4>
        {validation?.checks && validation.checks.length > 0 ? (
          <div className="space-y-1.5">
            {validation.checks.map((check: any, idx: number) => (
              <div key={idx} className="flex items-center justify-between py-1 border-b border-[var(--line)] last:border-none text-[11.5px]">
                <span className="text-[var(--ink-2)] truncate max-w-[180px]">{check.name || check.check}</span>
                <span className={check.passed ? "text-[var(--green-state)] font-medium" : "text-[var(--red-state)] font-medium"}>
                  {check.passed ? "✓ Pass" : "✗ Fail"}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-[11px] text-[var(--ink-4)]">No validation checks recorded.</p>
        )}
      </div>

      {/* Execution Actions */}
      <div className="rounded-lg border border-[var(--line)] bg-[var(--surface)] p-3">
        <h4 className="text-[12px] font-semibold text-[var(--ink)] mb-2">Execution & Controller</h4>
        {execution ? (
          <div className="space-y-2 text-[11.5px]">
            <div className="flex justify-between">
              <span className="text-[var(--ink-4)]">Status:</span>
              <span className="font-mono text-[var(--ink-2)]">{execution.status || "installed"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--ink-4)]">Flow Rules Installed:</span>
              <span className="font-mono text-[var(--ink-2)]">{execution.flow_rules_installed ?? 0}</span>
            </div>
            {((execution as any)?.path || (execution?.controller_response as any)?.path) && (
              <div>
                <span className="text-[var(--ink-4)] block mb-1">Rerouted Path:</span>
                <div className="font-mono text-[11px] p-1.5 rounded bg-[var(--sunken)] text-[var(--blue)]">
                  {((execution as any)?.path || (execution?.controller_response as any)?.path).join(" → ")}
                </div>
              </div>
            )}
          </div>
        ) : (
          <p className="text-[11px] text-[var(--ink-4)]">No execution actions performed yet.</p>
        )}
      </div>

      {/* Verification Assessment */}
      <div className="rounded-lg border border-[var(--line)] bg-[var(--surface)] p-3">
        <h4 className="text-[12px] font-semibold text-[var(--ink)] mb-2 flex items-center justify-between">
          <span>Verification Assessment</span>
          {verification && (
            <span
              className={`text-[10px] px-1.5 py-0.5 rounded font-semibold ${
                verification.intent_satisfied
                  ? "bg-[var(--green-soft)] text-[var(--green-state)]"
                  : "bg-[var(--yellow-soft)] text-[var(--yellow-state)]"
              }`}
            >
              {verification.intent_satisfied ? "Satisfied" : "Pending / Partial"}
            </span>
          )}
        </h4>
        {verification ? (
          <div className="space-y-2 text-[11.5px]">
            <div className="grid grid-cols-2 gap-2 p-2 rounded bg-[var(--sunken)]">
              <div>
                <span className="text-[10px] text-[var(--ink-4)] block">Latency</span>
                <span className="font-mono text-[11px] text-[var(--ink-2)]">
                  {formatMs(verification.before?.latency_ms)} → {formatMs(verification.after?.latency_ms)}
                </span>
              </div>
              <div>
                <span className="text-[10px] text-[var(--ink-4)] block">Packet Loss</span>
                <span className="font-mono text-[11px] text-[var(--ink-2)]">
                  {formatPct(verification.before?.packet_loss_pct, 1)} → {formatPct(verification.after?.packet_loss_pct, 1)}
                </span>
              </div>
            </div>
            {verification.reason && (
              <p className="text-[11px] text-[var(--ink-3)] italic">{verification.reason}</p>
            )}
          </div>
        ) : (
          <p className="text-[11px] text-[var(--ink-4)]">No verification performed.</p>
        )}
      </div>
    </div>
  );
}

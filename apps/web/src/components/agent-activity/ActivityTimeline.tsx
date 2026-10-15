"use client";

import React, { useState } from "react";
import type { ActivityGroup, ActivityTimelineProps } from "@/types";

/**
 * ActivityTimeline — the streamed run activity (prop contract documented
 * in `apps/web/types.ts`). Groups are expandable; each shows a concise
 * reasoning summary, never raw chain-of-thought.
 *
 * The prop shape follows the documented contract. Groups produced by
 * `lib/derive.groupEvents` additionally carry the raw event log in
 * `events`; when it is present the expanded group lists the observed
 * steps, otherwise it lists the recorded tool calls. Unknown status
 * values are rendered neutrally rather than dropped.
 */

const STATUS_DOT: Record<string, string> = {
  pending: "dot--idle",
  active: "dot--info dot--live",
  running: "dot--info dot--live",
  complete: "dot--ok",
  partial: "dot--warn",
  failed: "dot--bad",
  skipped: "dot--idle",
};

const STATUS_LABEL: Record<string, string> = {
  pending: "pending",
  active: "active",
  running: "running",
  complete: "complete",
  partial: "partial",
  failed: "failed",
  skipped: "skipped",
};

/** A group as the run store may pass it: contract fields plus the raw
 * event log the derive layer attaches. */
type TimelineGroup = ActivityGroup & {
  events?: readonly unknown[];
  core?: boolean;
};

interface RawEvent {
  readonly type?: string;
  readonly ts?: string;
  readonly tool?: string;
  readonly summary?: string;
  readonly message?: string;
  readonly error?: string;
  readonly delta?: string;
}

function clockOf(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const parsed = Date.parse(iso);
  if (!Number.isFinite(parsed)) return null;
  return new Date(parsed).toISOString().slice(11, 19);
}

export function ActivityTimeline({
  groups,
  expandedKeys,
  onToggleGroup,
  isStreaming,
  className,
}: ActivityTimelineProps) {
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set(expandedKeys ?? []));

  const toggle = (key: string) => {
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
    onToggleGroup?.(key);
  };

  return (
    <section className={`panel ${className ?? ""}`} aria-label="Agent activity">
      <header className="panel-head">
        <h2 className="panel-title">Agent activity</h2>
        <span className="panel-sub">streamed</span>
        <span className="flex-1" />
        {isStreaming ? (
          <span className="legend-item">
            <span className="dot dot--info dot--live" aria-hidden="true" />
            live
          </span>
        ) : null}
        <span className="panel-sub mono">{groups.length} groups</span>
      </header>

      <div className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto p-2">
        {groups.length === 0 ? (
          <div className="empty-state">
            <strong>No agent activity yet.</strong>
            <span>
              Intent detection, evidence collection, diagnosis, planning, safety checks, execution
              and verification stream here as the agent works.
            </span>
          </div>
        ) : (
          (groups as readonly TimelineGroup[]).map((group) => {
            const expanded = open.has(group.key);
            const status = typeof group.status === "string" ? group.status : "pending";
            const start = clockOf(group.startedAt);
            const finish = clockOf(group.finishedAt);
            const events = (group as TimelineGroup).events as readonly RawEvent[] | undefined;
            const steps = events ?? group.toolCalls ?? [];
            return (
              <div
                key={group.key}
                className="rounded-[var(--radius-sm)] border border-[var(--line)]"
                data-testid={`activity-group-${group.key}`}
              >
                <button
                  type="button"
                  className="flex w-full items-center gap-2 px-2 py-1.5 text-left hover:bg-[var(--sunken)]"
                  onClick={() => toggle(group.key)}
                  aria-expanded={expanded}
                >
                  <span className={`dot ${STATUS_DOT[status] ?? "dot--idle"}`} aria-hidden="true" />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-baseline gap-2">
                      <span className="text-[12px] font-medium text-[var(--ink)]">{group.label}</span>
                      {start ? (
                        <span className="mono text-[10.5px] text-[var(--ink-4)]">
                          {start}
                          {finish && finish !== start ? ` → ${finish}` : ""}
                        </span>
                      ) : null}
                    </span>
                    {group.summary ? (
                      <span className="mt-0.5 block truncate text-[11.5px] text-[var(--ink-3)]">
                        {group.summary}
                      </span>
                    ) : null}
                  </span>
                  <span className="text-[10.5px] uppercase tracking-wide text-[var(--ink-4)]">
                    {STATUS_LABEL[status] ?? status}
                  </span>
                  <span
                    aria-hidden="true"
                    className={`text-[var(--ink-4)] transition-transform ${expanded ? "rotate-90" : ""}`}
                  >
                    ▸
                  </span>
                </button>

                {expanded ? (
                  <div className="flex flex-col gap-1 border-t border-[var(--line)] px-3 py-2">
                    {steps.length === 0 ? (
                      <p className="text-[11.5px] text-[var(--ink-4)]">
                        No observable steps recorded for this group yet.
                      </p>
                    ) : (
                      steps.map((step, index) => {
                        const tool = (step as { tool?: string }).tool;
                        const summary = (step as { summary?: string }).summary;
                        const time = clockOf((step as { started_at?: string }).started_at);
                        return (
                          <div key={index} className="flex items-baseline gap-2 text-[11.5px]">
                            {time ? (
                              <span className="mono text-[10.5px] text-[var(--ink-4)]">{time}</span>
                            ) : null}
                            {tool ? (
                              <span className="mono text-[var(--ink-3)]">{tool}</span>
                            ) : null}
                            <span className="min-w-0 flex-1 truncate text-[var(--ink-3)]">
                              {summary ?? (step as { tool?: string }).tool ?? "observed"}
                            </span>
                            <span
                              className={`state-chip ${
                                (step as { status?: string }).status === "error" ||
                                (step as { status?: string }).status === "failed"
                                  ? "state-chip--red"
                                  : (step as { status?: string }).status === "skipped"
                                    ? "state-chip--amber"
                                    : "state-chip--green"
                              }`}
                            >
                              {(step as { status?: string }).status ?? "ok"}
                            </span>
                          </div>
                        );
                      })
                    )}
                  </div>
                ) : null}
              </div>
            );
          })
        )}
      </div>
    </section>
  );
}

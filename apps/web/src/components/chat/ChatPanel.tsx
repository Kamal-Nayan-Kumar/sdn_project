"use client";

import React, { useEffect, useRef, useState } from "react";
import type { AgentRunStatus, ChatMessage, ChatPanelProps } from "@/types";

/**
 * ChatPanel — real-time streaming conversational assistant.
 *
 * Renders user, assistant, and real-time streaming thinking / tool / guardrail
 * narrative updates. Supports markdown-clean rendering, tool action badges,
 * and live typing tokens.
 */

const ROLE_LABEL: Record<ChatMessage["role"], string> = {
  user: "Operator",
  assistant: "NetMind Agent",
  system: "Audit / Guardrail",
};

const ROLE_DOT: Record<ChatMessage["role"], string> = {
  user: "dot--info",
  assistant: "dot--ok",
  system: "dot--idle",
};

const STREAMING_STATUSES: ReadonlySet<AgentRunStatus> = new Set<AgentRunStatus>([
  "running",
  "gathering_evidence",
  "executing",
  "verifying",
]);

function statusTone(agentStatus: AgentRunStatus): string {
  switch (agentStatus) {
    case "complete":
      return "dot--ok";
    case "failed":
      return "dot--bad";
    case "insufficient_evidence":
    case "waiting_for_clarification":
    case "awaiting_approval":
      return "dot--warn";
    case "cancelled":
      return "dot--idle";
    default:
      return "dot--idle";
  }
}

export function ChatPanel({
  conversationId,
  messages,
  agentStatus,
  isStreaming,
  onSend,
  onStop,
  disabled,
  className,
}: ChatPanelProps) {
  const [draft, setDraft] = useState("");
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const busy = isStreaming || STREAMING_STATUSES.has(agentStatus);
  const canSend = !disabled && !busy && draft.trim().length > 0;

  // Auto-scroll to bottom as new tokens/messages arrive
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isStreaming]);

  const submit = () => {
    const text = draft.trim();
    if (!text || !canSend) return;
    setDraft("");
    void onSend(text);
  };

  return (
    <section className={`panel ${className ?? ""}`} aria-label="Assistant">
      <header className="panel-head flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h2 className="panel-title">Assistant</h2>
          <span className="panel-sub">conversation</span>
        </div>
        <div className="flex items-center gap-2">
          {conversationId ? (
            <span className="panel-sub mono text-[10.5px]" title={conversationId}>
              {conversationId}
            </span>
          ) : null}
          <span className={`dot ${statusTone(agentStatus)}`} aria-hidden="true" />
          <span className="panel-sub font-mono text-[11px]" data-testid="agent-streaming">
            {busy ? "streaming" : agentStatus === "idle" ? "idle" : agentStatus.replace(/_/g, " ")}
          </span>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3 text-[12.5px]">
        {messages.length === 0 ? (
          <div className="empty-state p-4 text-center rounded-lg border border-dashed border-[var(--line)] bg-[var(--surface-sunken)]">
            <strong className="block text-[13px] text-[var(--ink)] mb-1">Self-Healing SDN Operations</strong>
            <span className="text-[11.5px] text-[var(--ink-3)] block leading-relaxed max-w-[300px] mx-auto">
              Ask the agent to watch telemetry, diagnose link bottlenecks, or optimize latency under strict safety guardrails.
            </span>
            <div className="mt-3 flex flex-wrap gap-1.5 justify-center">
              <button
                type="button"
                onClick={() => void onSend("optimise video latency h1 to h4 so that it comes under 50 ms")}
                disabled={disabled || busy}
                className="rounded border border-[var(--line-strong)] bg-[var(--surface)] px-2 py-1 text-[11px] text-[var(--ink-2)] hover:bg-[var(--sunken)] transition-colors"
              >
                "optimise video latency h1 to h4 under 50 ms"
              </button>
            </div>
          </div>
        ) : (
          messages.map((message) => {
            const isUser = message.role === "user";
            const isSystem = message.role === "system";

            return (
              <article
                key={message.id}
                className={`flex flex-col gap-1.5 rounded-lg border px-3 py-2.5 transition-all ${
                  isUser
                    ? "bg-[var(--sunken)] border-[var(--line-strong)] self-end max-w-[90%]"
                    : isSystem
                    ? "bg-[var(--surface-sunken)] border-[var(--line)] border-l-2 border-l-[var(--brand)] text-[11.5px]"
                    : "bg-[var(--surface)] border-[var(--line)] shadow-xs"
                }`}
              >
                <header className="flex items-center justify-between text-[11px]">
                  <div className="flex items-center gap-1.5 font-medium">
                    <span className={`dot ${ROLE_DOT[message.role]}`} aria-hidden="true" />
                    <span className={isUser ? "text-[var(--ink)]" : "text-[var(--brand)] font-semibold"}>
                      {ROLE_LABEL[message.role]}
                    </span>
                    {message.streaming && (
                      <span className="animate-pulse text-[var(--brand)] font-mono text-[10px]">generating…</span>
                    )}
                  </div>
                  {message.outcome && (
                    <span className="rounded bg-[var(--surface)] px-1.5 py-0.2 font-mono text-[10px] text-[var(--ink-4)] border border-[var(--line)]">
                      {message.outcome}
                    </span>
                  )}
                </header>

                <div className="whitespace-pre-wrap leading-relaxed text-[var(--ink-2)]">
                  {message.content}
                </div>
              </article>
            );
          })
        )}

        {busy && (
          <div className="flex items-center gap-2 text-[11.5px] text-[var(--ink-3)] p-1">
            <span className="dot dot--info dot--live animate-ping" aria-hidden="true" />
            <span className="font-mono text-[11px]">Agent thinking & executing tools…</span>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className="hairline" />

      <form
        className="flex items-center gap-2 p-2.5 bg-[var(--surface)] border-t border-[var(--line)]"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <input
          type="text"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={disabled ? "Backend offline — messages disabled" : "Submit natural language network intent…"}
          disabled={disabled || busy}
          aria-label="Message the agent"
          className="h-9 min-w-0 flex-1 rounded-md border border-[var(--line-strong)] bg-[var(--surface)] px-3 text-[12.5px] text-[var(--ink)] outline-none placeholder:text-[var(--ink-4)] focus:border-[var(--brand)] disabled:cursor-not-allowed disabled:bg-[var(--sunken)] disabled:text-[var(--ink-4)]"
        />
        <button
          type="submit"
          className="btn btn--primary h-9 px-3.5 rounded-md text-[12px] font-medium"
          disabled={!canSend}
          title="Send the message to the agent"
        >
          Send
        </button>
        {busy && onStop ? (
          <button
            type="button"
            className="btn btn--danger h-9 px-2.5 rounded-md text-[12px]"
            onClick={() => onStop()}
            title="Stop the current run"
          >
            Stop
          </button>
        ) : null}
      </form>
    </section>
  );
}

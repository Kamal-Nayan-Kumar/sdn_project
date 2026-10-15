"use client";

import React, { useState } from "react";
import type { AgentRunStatus, ChatMessage, ChatPanelProps } from "@/types";

/**
 * ChatPanel — the assistant conversation (prop contract documented in
 * `apps/web/types.ts`). Renders message history only; nothing here
 * fabricates a reply. Messages are sent through `onSend`, which the
 * run store owns, and the stop button is shown only while a run is
 * actually streaming.
 */

const ROLE_LABEL: Record<ChatMessage["role"], string> = {
  user: "you",
  assistant: "agent",
  system: "system",
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
  const busy = isStreaming || STREAMING_STATUSES.has(agentStatus);
  const canSend = !disabled && !busy && draft.trim().length > 0;

  const submit = () => {
    const text = draft.trim();
    if (!text || !canSend) return;
    setDraft("");
    void onSend(text);
  };

  return (
    <section className={`panel ${className ?? ""}`} aria-label="Assistant">
      <header className="panel-head">
        <h2 className="panel-title">Assistant</h2>
        <span className="panel-sub">conversation</span>
        <span className="flex-1" />
        {conversationId ? (
          <span className="panel-sub mono" title={conversationId}>
            {conversationId}
          </span>
        ) : null}
        <span className={`dot ${statusTone(agentStatus)}`} aria-hidden="true" />
        <span className="panel-sub" data-testid="agent-streaming">
          {busy ? "streaming" : agentStatus === "idle" ? "idle" : agentStatus.replace(/_/g, " ")}
        </span>
      </header>

      <div className="flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto p-3">
        {messages.length === 0 ? (
          <div className="empty-state">
            <strong>No conversation yet.</strong>
            <span>
              Ask the agent to watch, diagnose or recover the lab. Every answer is grounded in the
              live snapshot — the agent invents nothing.
            </span>
          </div>
        ) : (
          messages.map((message) => (
            <article
              key={message.id}
              className={`flex flex-col gap-1 rounded-[var(--radius-sm)] border border-[var(--line)] px-2.5 py-2 ${
                message.role === "user" ? "bg-[var(--sunken)]" : "bg-[var(--surface)]"
              }`}
            >
              <header className="flex items-center gap-1.5">
                <span className={`dot ${ROLE_DOT[message.role]}`} aria-hidden="true" />
                <span className="text-[11px] font-medium text-[var(--ink-3)]">
                  {ROLE_LABEL[message.role]}
                </span>
                {message.streaming ? <span className="text-[11px] text-[var(--ink-4)]">…</span> : null}
                {message.outcome ? (
                  <span className="text-[11px] text-[var(--ink-4)]">{message.outcome}</span>
                ) : null}
              </header>
              <p className="whitespace-pre-wrap text-[12.5px] leading-relaxed text-[var(--ink-2)]">
                {message.content}
              </p>
            </article>
          ))
        )}
        {busy ? (
          <p className="flex items-center gap-1.5 text-[11.5px] text-[var(--ink-3)]">
            <span className="dot dot--info dot--live" aria-hidden="true" />
            {isStreaming ? "The agent is answering…" : "The agent is working…"}
          </p>
        ) : null}
      </div>

      <div className="hairline" />

      <form
        className="flex items-center gap-1.5 p-2"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <input
          type="text"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={disabled ? "Backend offline — messages disabled" : "Ask about the network…"}
          disabled={disabled || busy}
          aria-label="Message the agent"
          className="h-7 min-w-0 flex-1 rounded-[var(--radius-sm)] border border-[var(--line-strong)] bg-[var(--surface)] px-2 text-[12.5px] text-[var(--ink)] outline-none placeholder:text-[var(--ink-4)] disabled:cursor-not-allowed disabled:bg-[var(--sunken)] disabled:text-[var(--ink-4)]"
        />
        <button
          type="submit"
          className="btn btn--primary"
          disabled={!canSend}
          title="Send the message to the agent"
        >
          Send
        </button>
        {busy && onStop ? (
          <button
            type="button"
            className="btn btn--danger"
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

"use client";
import React, { useCallback, useRef, useState } from "react";

import { ActivityTimeline } from "@/components/agent-activity/ActivityTimeline";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { RunDetailsPanel } from "./RunDetailsPanel";
import type { ActivityGroup, AgentRunStatus, ChatMessage, ActivityStepKey } from "@/types";

/**
 * Right-hand sidebar region: the conversation and the streamed agent
 * activity (spec section 9, "Chat sidebar").
 *
 * Both panels are real components (contracts in `apps/web/types.ts`).
 * They connect to the backend via /api/runs and SSE streaming.
 */

import { useLabData } from "./lab-data";

export function Sidebar() {
  const { setCurrentRun } = useLabData();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [agentStatus, setAgentStatus] = useState<AgentRunStatus>("idle");
  const [isStreaming, setIsStreaming] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [activityGroups, setActivityGroups] = useState<ActivityGroup[]>([]);
  const runIdRef = useRef<string | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const msgCounterRef = useRef(0);
  const nextMsgId = useCallback(() => `msg-${Date.now()}-${msgCounterRef.current++}`, []);

  const handleStop = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    setIsStreaming(false);
    setAgentStatus("cancelled");
  }, []);

  const handleSend = useCallback(async (text: string) => {
    try {
      const response = await fetch("/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });

      if (!response.ok) {
        throw new Error(`Failed to send message: ${response.status}`);
      }

      const data = await response.json();
      const runId = data.run_id;
      runIdRef.current = runId;
      setConversationId(runId);
      setAgentStatus("running");
      setIsStreaming(true);

      // Add user message with all required fields
      const userMessage: ChatMessage = {
        id: nextMsgId(),
        role: "user",
        content: text,
        createdAt: new Date().toISOString(),
        streaming: false,
        runId: runId,
        outcome: null,
        attachments: [],
      };
      setMessages((prev) => [...prev, userMessage]);

      // Connect to the SSE stream.
      //
      // Every frame the backend writes carries an explicit `event:` name
      // (`event: run_completed`, `event: validation_completed`, ...). A named
      // SSE event is dispatched only to listeners registered for that name, so
      // `onmessage` -- which receives only the default, unnamed `message`
      // event -- never fires. Registering per type is what makes the stream
      // visible in the UI at all.
      const eventSource = new EventSource(`/api/runs/${runId}/stream`);
      eventSourceRef.current = eventSource;

      const handleFrame = (parsed: Record<string, any>) => {
        const eventType = parsed.type;

        if (eventType === "run_started") {
          // Run started - initial state already set
        } else if (eventType === "step_started") {
          // Update activity group status
          const stepKey = parsed.step;
          setActivityGroups((prev) => {
            const existingIndex = prev.findIndex((g) => g.key === stepKey);
            if (existingIndex >= 0) {
              const updated = [...prev];
              updated[existingIndex] = {
                ...updated[existingIndex],
                status: "active",
                startedAt: new Date(parsed.timestamp * 1000).toISOString(),
              };
              return updated;
            }
            // Add new group
            const stepLabels: Record<ActivityStepKey, string> = {
              intent_detection: "Intent Detection",
              evidence_collection: "Evidence Collection",
              diagnosis_summary: "Diagnosis",
              recovery_planning: "Recovery Planning",
              tool_calls: "Tool Calls",
              safety_validation: "Safety Validation",
              execution: "Execution",
              recovery_verification: "Recovery Verification",
            };
            return [
              ...prev,
              {
                key: stepKey,
                label: stepLabels[stepKey] || stepKey,
                status: "active",
                startedAt: new Date(parsed.timestamp * 1000).toISOString(),
                finishedAt: null,
              },
            ];
          });
        } else if (eventType === "step_completed") {
          // Mark step as complete
          const stepKey = parsed.step;
          setActivityGroups((prev) =>
            prev.map((g) =>
              g.key === stepKey
                ? { ...g, status: "complete", finishedAt: new Date(parsed.timestamp * 1000).toISOString() }
                : g
            )
          );
        } else if (eventType === "run_completed") {
          setIsStreaming(false);
          setAgentStatus("complete");
          eventSource.close();

          // Fetch full enriched run record to update Dashboard panels (audit, verified path, metrics)
          fetch(`/api/runs/${runId}`)
            .then((res) => (res.ok ? res.json() : null))
            .then((data) => {
              if (data) setCurrentRun(data);
            })
            .catch((err) => console.error("Failed to load run details:", err));

          // Add final assistant message with explanation
          const newMessage: ChatMessage = {
            id: nextMsgId(),
            role: "assistant",
            content: (parsed.payload?.explanation as string) || "Run completed",
            createdAt: new Date().toISOString(),
            streaming: false,
            runId: runId,
            outcome: ((parsed.payload?.outcome as string) || "complete") as any,
            attachments: [],
          };
          setMessages((prev) => [...prev, newMessage]);
        } else if (eventType === "run_failed" || eventType === "run_cancelled") {
          setIsStreaming(false);
          setAgentStatus(eventType === "run_failed" ? "failed" : "cancelled");
          eventSource.close();

          fetch(`/api/runs/${runId}`)
            .then((res) => (res.ok ? res.json() : null))
            .then((data) => {
              if (data) setCurrentRun(data);
            })
            .catch((err) => console.error("Failed to load run details:", err));

          const newMessage: ChatMessage = {
            id: nextMsgId(),
            role: "assistant",
            content: (parsed.payload?.error as string) || `Run ${eventType === "run_failed" ? "failed" : "cancelled"}`,
            createdAt: new Date().toISOString(),
            streaming: false,
            runId: runId,
            outcome: eventType,
            attachments: [],
          };
          setMessages((prev) => [...prev, newMessage]);
        } else if (eventType === "intent_detected") {
          const payload = parsed.payload || {};
          const content = `Intent detected: ${payload.src_host} -> ${payload.dst_host}, max latency: ${payload.max_latency_ms}ms, traffic class: ${payload.traffic_class}`;
          setMessages((prev) => [
            ...prev,
            {
              id: nextMsgId(),
              role: "system",
              content,
              createdAt: new Date().toISOString(),
              streaming: false,
              runId,
              outcome: null,
              attachments: [],
            },
          ]);
        } else if (eventType === "evidence_assessed") {
          const payload = parsed.payload || {};
          const content = `Evidence: ${payload.summary || "collected"} (sufficient: ${payload.sufficient}, confidence: ${payload.confidence})`;
          setMessages((prev) => [
            ...prev,
            {
              id: nextMsgId(),
              role: "system",
              content,
              createdAt: new Date().toISOString(),
              streaming: false,
              runId,
              outcome: null,
              attachments: [],
            },
          ]);
        } else if (eventType === "diagnosis_summary") {
          const payload = parsed.payload || {};
          const content = `Diagnosis: ${payload.summary || "completed"}`;
          setMessages((prev) => [
            ...prev,
            {
              id: nextMsgId(),
              role: "system",
              content,
              createdAt: new Date().toISOString(),
              streaming: false,
              runId,
              outcome: null,
              attachments: [],
            },
          ]);
        } else if (eventType === "validation_completed") {
          const payload = parsed.payload || {};
          const content = `Safety validation: ${payload.status} (risk: ${payload.risk_level}, checks passed: ${payload.checks_passed})`;
          setMessages((prev) => [
            ...prev,
            {
              id: nextMsgId(),
              role: "system",
              content,
              createdAt: new Date().toISOString(),
              streaming: false,
              runId,
              outcome: null,
              attachments: [],
            },
          ]);
        } else if (eventType === "execution_completed") {
          const payload = parsed.payload || {};
          const content = `Execution: ${payload.status} (rules installed: ${payload.flow_rules_installed})`;
          setMessages((prev) => [
            ...prev,
            {
              id: nextMsgId(),
              role: "system",
              content,
              createdAt: new Date().toISOString(),
              streaming: false,
              runId,
              outcome: null,
              attachments: [],
            },
          ]);
        } else if (eventType === "verification_completed") {
          const payload = parsed.payload || {};
          const content = `Verification: ${payload.status} (intent satisfied: ${payload.intent_satisfied}, before: ${payload.before_ms}ms, after: ${payload.after_ms}ms)`;
          setMessages((prev) => [
            ...prev,
            {
              id: nextMsgId(),
              role: "system",
              content,
              createdAt: new Date().toISOString(),
              streaming: false,
              runId,
              outcome: null,
              attachments: [],
            },
          ]);
        }
      };

      // One listener per event type the backend can emit, all sharing
      // `handleFrame`. `message` is included so an unnamed frame still works
      // if the backend ever emits one.
      const EVENT_TYPES = [
        "message",
        "run_started",
        "run_completed",
        "run_failed",
        "run_cancelled",
        "step_started",
        "step_completed",
        "intent_detected",
        "evidence_assessed",
        "diagnosis_summary",
        "validation_completed",
        "execution_completed",
        "verification_completed",
        "tool_started",
        "tool_completed",
        "note",
      ] as const;

      const listeners = EVENT_TYPES.map((type) => {
        const listener = (event: MessageEvent<string>) => {
          try {
            const parsed = JSON.parse(event.data);
            console.log("SSE event:", type, parsed);
            handleFrame(parsed);
          } catch (e) {
            console.error("Failed to parse SSE event:", e, event);
          }
        };
        eventSource.addEventListener(type, listener as EventListener);
        return [type, listener] as const;
      });

      eventSource.onerror = () => {
        // ReadyState CLOSED after a terminal event is a normal end of stream,
        // not a failure. Only a genuine drop is reported as one.
        if (eventSource.readyState === EventSource.CLOSED) {
          eventSource.close();
          setIsStreaming(false);
          setAgentStatus((prev) => (prev === "idle" ? "idle" : prev));
          return;
        }
        eventSource.close();
        setIsStreaming(false);
        setAgentStatus("failed");
      };
    } catch (error) {
      console.error("Failed to send message:", error);
      setAgentStatus("failed");
    }
  }, []);

  const [activeTab, setActiveTab] = useState<"chat" | "activity" | "details">("chat");

  return (
    <aside className="app-sidebar flex flex-col bg-[var(--surface)] border-l border-[var(--line)]" id="region-chat" aria-label="Assistant and agent activity">
      {/* Top Header Tabs: Chat, Agent Activity, Run Details */}
      <div className="flex items-center justify-between border-b border-[var(--line)] px-3 pt-2">
        <div className="flex gap-4">
          <button
            type="button"
            onClick={() => setActiveTab("chat")}
            className={`pb-2.5 text-[13px] font-semibold transition-colors relative ${
              activeTab === "chat" ? "text-[var(--brand)]" : "text-[var(--ink-3)] hover:text-[var(--ink)]"
            }`}
          >
            Chat
            {activeTab === "chat" && (
              <span className="absolute bottom-0 left-0 right-0 h-[2px] bg-[var(--brand)] rounded-full" />
            )}
          </button>

          <button
            type="button"
            onClick={() => setActiveTab("activity")}
            className={`pb-2.5 text-[13px] font-medium transition-colors relative ${
              activeTab === "activity" ? "text-[var(--brand)] font-semibold" : "text-[var(--ink-3)] hover:text-[var(--ink)]"
            }`}
          >
            Agent Activity
            {activeTab === "activity" && (
              <span className="absolute bottom-0 left-0 right-0 h-[2px] bg-[var(--brand)] rounded-full" />
            )}
          </button>

          <button
            type="button"
            onClick={() => setActiveTab("details")}
            className={`pb-2.5 text-[13px] font-medium transition-colors relative ${
              activeTab === "details" ? "text-[var(--brand)] font-semibold" : "text-[var(--ink-3)] hover:text-[var(--ink)]"
            }`}
          >
            Run Details
            {activeTab === "details" && (
              <span className="absolute bottom-0 left-0 right-0 h-[2px] bg-[var(--brand)] rounded-full" />
            )}
          </button>
        </div>

        <button
          type="button"
          className="text-[var(--ink-4)] hover:text-[var(--ink-2)] pb-2"
          title="Expand"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polyline points="15 3 21 3 21 9" />
            <polyline points="9 21 3 21 3 15" />
            <line x1="21" y1="3" x2="14" y2="10" />
            <line x1="3" y1="21" x2="10" y2="14" />
          </svg>
        </button>
      </div>

      {/* Main Tab Content */}
      <div className="flex min-h-0 flex-1 flex-col gap-2 p-2 overflow-y-auto">
        {activeTab === "chat" && (
          <ChatPanel
            conversationId={conversationId}
            messages={messages}
            agentStatus={agentStatus}
            isStreaming={isStreaming}
            onSend={handleSend}
            onStop={handleStop}
            disabled={false}
            className="flex-1"
          />
        )}
        {activeTab === "activity" && (
          <ActivityTimeline
            groups={activityGroups}
            isStreaming={isStreaming}
            className="flex-1"
          />
        )}
        {activeTab === "details" && <RunDetailsPanel />}
      </div>
    </aside>
  );
}

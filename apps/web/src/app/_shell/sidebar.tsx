"use client";
import React, { useCallback, useRef, useState } from "react";

import { ActivityTimeline } from "@/components/agent-activity/ActivityTimeline";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { RunDetailsPanel } from "./RunDetailsPanel";
import type { ActivityGroup, AgentRunStatus, ChatMessage, ActivityStepKey } from "@/types";
import { useLabData } from "./lab-data";

/**
 * Right-hand sidebar region: real-time streaming conversation, tool telemetry,
 * and structured run activity.
 */

export function Sidebar() {
  const { setCurrentRun, setAgentStatus: setGlobalAgentStatus } = useLabData();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [agentStatus, setLocalAgentStatus] = useState<AgentRunStatus>("idle");
  const [isStreaming, setIsStreaming] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [activityGroups, setActivityGroups] = useState<ActivityGroup[]>([]);
  const runIdRef = useRef<string | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const msgCounterRef = useRef(0);
  const nextMsgId = useCallback(() => `msg-${Date.now()}-${msgCounterRef.current++}`, []);

  const updateStatus = useCallback((status: AgentRunStatus) => {
    setLocalAgentStatus(status);
    setGlobalAgentStatus(status);
  }, [setGlobalAgentStatus]);

  const handleStop = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    setIsStreaming(false);
    updateStatus("cancelled");
  }, [updateStatus]);

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
      updateStatus("running");
      setIsStreaming(true);

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

      const eventSource = new EventSource(`/api/runs/${runId}/stream`);
      eventSourceRef.current = eventSource;

      // Assistant message tracking for live token streaming
      let assistantMsgId: string | null = null;

      const handleFrame = (parsed: Record<string, any>) => {
        const eventType = parsed.type;

        if (eventType === "step_started") {
          const rawStep = parsed.step;
          const stepMap: Record<string, ActivityStepKey> = {
            intent: "intent_detection",
            observe: "evidence_collection",
            evidence: "evidence_collection",
            diagnose: "diagnosis_summary",
            validate: "safety_validation",
            execute: "execution",
            observe_after: "recovery_verification",
            verify: "recovery_verification",
            answer: "diagnosis_summary",
          };
          const stepKey: ActivityStepKey = stepMap[rawStep] || "diagnosis_summary";
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
            const stepLabels: Record<ActivityStepKey, string> = {
              intent_detection: "Intent Detection",
              evidence_collection: "Evidence Collection",
              diagnosis_summary: "Diagnosis & Planning",
              recovery_planning: "Recovery Planning",
              tool_calls: "Tool Invocations",
              safety_validation: "Safety Gate Validation",
              execution: "Data Plane Execution",
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
          const rawStep = parsed.step;
          const stepMap: Record<string, ActivityStepKey> = {
            intent: "intent_detection",
            observe: "evidence_collection",
            evidence: "evidence_collection",
            diagnose: "diagnosis_summary",
            validate: "safety_validation",
            execute: "execution",
            observe_after: "recovery_verification",
            verify: "recovery_verification",
            answer: "diagnosis_summary",
          };
          const stepKey: ActivityStepKey = stepMap[rawStep] || "diagnosis_summary";
          setActivityGroups((prev) =>
            prev.map((g) =>
              g.key === stepKey
                ? {
                    ...g,
                    status: "complete",
                    summary: parsed.payload?.summary || g.summary,
                    finishedAt: new Date(parsed.timestamp * 1000).toISOString(),
                  }
                : g
            )
          );
        } else if (eventType === "thinking") {
          // Model live thought streaming (diagnosis / reasoning)
          const chunk = parsed.payload?.text || "";
          setActivityGroups((prev) => {
            const diagGroup = prev.find((g) => g.key === "diagnosis_summary");
            if (diagGroup) {
              return prev.map((g) =>
                g.key === "diagnosis_summary"
                  ? { ...g, summary: (g.summary ? g.summary + chunk : chunk).slice(-120) }
                  : g
              );
            }
            return prev;
          });
        } else if (eventType === "token") {
          // Live token streaming for the final assistant explanation
          const delta = parsed.payload?.text || "";
          if (!assistantMsgId) {
            assistantMsgId = nextMsgId();
            setMessages((prev) => [
              ...prev,
              {
                id: assistantMsgId!,
                role: "assistant",
                content: delta,
                createdAt: new Date().toISOString(),
                streaming: true,
                runId: runId,
                outcome: null,
                attachments: [],
              },
            ]);
          } else {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantMsgId
                  ? { ...m, content: m.content + delta, streaming: true }
                  : m
              )
            );
          }
        } else if (eventType === "tool_started") {
          const tool = parsed.payload?.tool;
          setMessages((prev) => [
            ...prev,
            {
              id: nextMsgId(),
              role: "system",
              content: `🔧 Invoking tool: ${tool}`,
              createdAt: new Date().toISOString(),
              streaming: false,
              runId,
              outcome: null,
              attachments: [],
            },
          ]);
        } else if (eventType === "tool_completed") {
          const payload = parsed.payload || {};
          setMessages((prev) => [
            ...prev,
            {
              id: nextMsgId(),
              role: "system",
              content: `✓ Tool ${payload.tool} returned (${payload.duration_ms}ms): ${payload.summary || "ok"}`,
              createdAt: new Date().toISOString(),
              streaming: false,
              runId,
              outcome: null,
              attachments: [],
            },
          ]);
        } else if (eventType === "guardrail") {
          const payload = parsed.payload || {};
          setMessages((prev) => [
            ...prev,
            {
              id: nextMsgId(),
              role: "system",
              content: `🛡️ Guardrail [${payload.stage}]: ${payload.decision === "allow" ? "Passed" : "Blocked"} (${payload.rule}) — ${payload.reason}`,
              createdAt: new Date().toISOString(),
              streaming: false,
              runId,
              outcome: null,
              attachments: [],
            },
          ]);
        } else if (eventType === "run_completed") {
          setIsStreaming(false);
          updateStatus("complete");
          eventSource.close();

          fetch(`/api/runs/${runId}`)
            .then((res) => (res.ok ? res.json() : null))
            .then((data) => {
              if (data) setCurrentRun(data);
            })
            .catch((err) => console.error("Failed to load run details:", err));

          // Seal the streaming assistant message if tokens were already written
          if (assistantMsgId) {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantMsgId
                  ? { ...m, streaming: false, outcome: (parsed.payload?.outcome as any) || "complete" }
                  : m
              )
            );
          } else {
            const finalAnswer = (parsed.payload?.answer as string) || (parsed.payload?.explanation as string) || "Run completed";
            setMessages((prev) => [
              ...prev,
              {
                id: nextMsgId(),
                role: "assistant",
                content: finalAnswer,
                createdAt: new Date().toISOString(),
                streaming: false,
                runId: runId,
                outcome: (parsed.payload?.outcome as any) || "complete",
                attachments: [],
              },
            ]);
          }
        } else if (eventType === "run_failed" || eventType === "run_cancelled") {
          setIsStreaming(false);
          updateStatus(eventType === "run_failed" ? "failed" : "cancelled");
          eventSource.close();

          fetch(`/api/runs/${runId}`)
            .then((res) => (res.ok ? res.json() : null))
            .then((data) => {
              if (data) setCurrentRun(data);
            })
            .catch((err) => console.error("Failed to load run details:", err));

          const errorMsg = (parsed.payload?.error as string) || (parsed.payload?.reason as string) || `Run ${eventType}`;
          setMessages((prev) => [
            ...prev,
            {
              id: nextMsgId(),
              role: "assistant",
              content: errorMsg,
              createdAt: new Date().toISOString(),
              streaming: false,
              runId: runId,
              outcome: eventType,
              attachments: [],
            },
          ]);
        }
      };

      const EVENT_TYPES = [
        "message",
        "run_started",
        "run_completed",
        "run_failed",
        "run_cancelled",
        "step_started",
        "step_completed",
        "thinking",
        "token",
        "tool_started",
        "tool_completed",
        "guardrail",
        "intent_detected",
        "evidence_assessed",
        "diagnosis_summary",
        "validation_completed",
        "execution_completed",
        "verification_completed",
        "note",
      ] as const;

      EVENT_TYPES.forEach((type) => {
        eventSource.addEventListener(type, (event: MessageEvent<string>) => {
          try {
            const parsed = JSON.parse(event.data);
            handleFrame(parsed);
          } catch (e) {
            console.error("Failed to parse SSE event:", e, event);
          }
        });
      });

      eventSource.onerror = () => {
        if (eventSource.readyState === EventSource.CLOSED) {
          eventSource.close();
          setIsStreaming(false);
          updateStatus("idle");
          return;
        }
        eventSource.close();
        setIsStreaming(false);
        updateStatus("failed");
      };
    } catch (error) {
      console.error("Failed to send message:", error);
      updateStatus("failed");
    }
  }, [nextMsgId, setCurrentRun, updateStatus]);

  const [activeTab, setActiveTab] = useState<"chat" | "activity" | "details">("chat");

  return (
    <aside className="app-sidebar flex flex-col bg-[var(--surface)] border-l border-[var(--line)]" id="region-chat" aria-label="Assistant and agent activity">
      {/* Top Header Tabs: Chat, Agent Activity, Run Details */}
      <div className="flex items-center justify-between border-b border-[var(--line)] px-3 pt-2">
        <div className="flex gap-4">
          <button
            type="button"
            onClick={() => setActiveTab("chat")}
            className={`pb-2 text-[12.5px] font-semibold transition-colors relative ${
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
            className={`pb-2 text-[12.5px] font-medium transition-colors relative ${
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
            className={`pb-2 text-[12.5px] font-medium transition-colors relative ${
              activeTab === "details" ? "text-[var(--brand)] font-semibold" : "text-[var(--ink-3)] hover:text-[var(--ink)]"
            }`}
          >
            Run Details
            {activeTab === "details" && (
              <span className="absolute bottom-0 left-0 right-0 h-[2px] bg-[var(--brand)] rounded-full" />
            )}
          </button>
        </div>
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

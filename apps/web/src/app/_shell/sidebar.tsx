"use client";
import React, { useCallback, useState } from "react";

import { ActivityTimeline } from "@/components/agent-activity/ActivityTimeline";
import { ChatPanel } from "@/components/chat/ChatPanel";
import type { ActivityGroup, AgentRunStatus, ChatMessage, ActivityStepKey } from "@/types";

/**
 * Right-hand sidebar region: the conversation and the streamed agent
 * activity (spec section 9, "Chat sidebar").
 *
 * Both panels are real components (contracts in `apps/web/types.ts`).
 * They connect to the backend via /api/runs and SSE streaming.
 */

export function Sidebar() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [agentStatus, setAgentStatus] = useState<AgentRunStatus>("idle");
  const [isStreaming, setIsStreaming] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [activityGroups, setActivityGroups] = useState<ActivityGroup[]>([]);

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
      setConversationId(runId);
      setAgentStatus("running");
      setIsStreaming(true);

      // Add user message with all required fields
      const userMessage: ChatMessage = {
        id: `msg-${Date.now()}`,
        role: "user",
        content: text,
        createdAt: new Date().toISOString(),
        streaming: false,
        runId: runId,
        outcome: null,
        attachments: [],
      };
      setMessages((prev) => [...prev, userMessage]);

      // Connect to SSE stream
      const eventSource = new EventSource(`/api/runs/${runId}/stream`);

      eventSource.onmessage = (event) => {
        try {
          const parsed = JSON.parse(event.data);
          const eventType = parsed.type;
          
          console.log("SSE event:", eventType, parsed);

          if (eventType === "run_started") {
            // Run started - initial state already set
          } else if (eventType === "step_started") {
            // Update activity group status
            setActivityGroups((prev) => {
              const stepKey = parsed.step as ActivityStepKey;
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
            setActivityGroups((prev) => {
              const stepKey = parsed.step as ActivityStepKey;
              return prev.map((g) =>
                g.key === stepKey
                  ? { ...g, status: "complete", finishedAt: new Date(parsed.timestamp * 1000).toISOString() }
                  : g
              );
            });
          } else if (eventType === "run_completed") {
            setIsStreaming(false);
            setAgentStatus("complete");
            eventSource.close();
            
            // Add final assistant message with explanation
            const explanation = parsed.payload?.explanation || "Run completed";
            const newMessage: ChatMessage = {
              id: `msg-${Date.now()}`,
              role: "assistant",
              content: explanation,
              createdAt: new Date().toISOString(),
              streaming: false,
              runId: runId,
              outcome: parsed.payload?.outcome || "complete",
              attachments: [],
            };
            setMessages((prev) => [...prev, newMessage]);
          } else if (eventType === "run_failed") {
            setIsStreaming(false);
            setAgentStatus("failed");
            eventSource.close();
            
            const newMessage: ChatMessage = {
              id: `msg-${Date.now()}`,
              role: "assistant",
              content: parsed.payload?.error || "Run failed",
              createdAt: new Date().toISOString(),
              streaming: false,
              runId: runId,
              outcome: "failed",
              attachments: [],
            };
            setMessages((prev) => [...prev, newMessage]);
          } else if (eventType === "intent_detected") {
            // Intent detected - add a system message
            const payload = parsed.payload || {};
            const content = `Intent detected: ${payload.src_host} -> ${payload.dst_host}, max latency: ${payload.max_latency_ms}ms, traffic class: ${payload.traffic_class}`;
            const newMessage: ChatMessage = {
              id: `msg-${Date.now()}`,
              role: "system",
              content,
              createdAt: new Date().toISOString(),
              streaming: false,
              runId: runId,
              outcome: null,
              attachments: [],
            };
            setMessages((prev) => [...prev, newMessage]);
          } else if (eventType === "evidence_assessed") {
            // Evidence assessed
            const payload = parsed.payload || {};
            const content = `Evidence: ${payload.summary || "collected"} (sufficient: ${payload.sufficient}, confidence: ${payload.confidence})`;
            const newMessage: ChatMessage = {
              id: `msg-${Date.now()}`,
              role: "system",
              content,
              createdAt: new Date().toISOString(),
              streaming: false,
              runId: runId,
              outcome: null,
              attachments: [],
            };
            setMessages((prev) => [...prev, newMessage]);
          } else if (eventType === "diagnosis_summary") {
            // Diagnosis summary
            const payload = parsed.payload || {};
            const content = `Diagnosis: ${payload.summary || "completed"}`;
            const newMessage: ChatMessage = {
              id: `msg-${Date.now()}`,
              role: "system",
              content,
              createdAt: new Date().toISOString(),
              streaming: false,
              runId: runId,
              outcome: null,
              attachments: [],
            };
            setMessages((prev) => [...prev, newMessage]);
          } else if (eventType === "validation_completed") {
            // Validation completed
            const payload = parsed.payload || {};
            const content = `Safety validation: ${payload.status} (risk: ${payload.risk_level}, checks passed: ${payload.checks_passed})`;
            const newMessage: ChatMessage = {
              id: `msg-${Date.now()}`,
              role: "system",
              content,
              createdAt: new Date().toISOString(),
              streaming: false,
              runId: runId,
              outcome: null,
              attachments: [],
            };
            setMessages((prev) => [...prev, newMessage]);
          } else if (eventType === "execution_completed") {
            // Execution completed
            const payload = parsed.payload || {};
            const content = `Execution: ${payload.status} (rules installed: ${payload.flow_rules_installed})`;
            const newMessage: ChatMessage = {
              id: `msg-${Date.now()}`,
              role: "system",
              content,
              createdAt: new Date().toISOString(),
              streaming: false,
              runId: runId,
              outcome: null,
              attachments: [],
            };
            setMessages((prev) => [...prev, newMessage]);
          } else if (eventType === "verification_completed") {
            // Verification completed
            const payload = parsed.payload || {};
            const content = `Verification: ${payload.status} (intent satisfied: ${payload.intent_satisfied}, before: ${payload.before_ms}ms, after: ${payload.after_ms}ms)`;
            const newMessage: ChatMessage = {
              id: `msg-${Date.now()}`,
              role: "system",
              content,
              createdAt: new Date().toISOString(),
              streaming: false,
              runId: runId,
              outcome: null,
              attachments: [],
            };
            setMessages((prev) => [...prev, newMessage]);
          }
        } catch (e) {
          console.error("Failed to parse SSE event:", e, event);
        }
      };

      eventSource.onerror = () => {
        eventSource.close();
        setIsStreaming(false);
        setAgentStatus("failed");
      };
    } catch (error) {
      console.error("Failed to send message:", error);
      setAgentStatus("failed");
    }
  }, []);

  const handleStop = useCallback(() => {
    setIsStreaming(false);
    setAgentStatus("cancelled");
    // TODO: Call backend to stop the run via DELETE /api/runs/{run_id}
  }, []);

  return (
    <aside className="app-sidebar" id="region-chat" aria-label="Assistant and agent activity">
      <div className="flex min-h-0 flex-1 flex-col gap-2 p-2">
        <ChatPanel
          conversationId={conversationId}
          messages={messages}
          agentStatus={agentStatus}
          isStreaming={isStreaming}
          onSend={handleSend}
          onStop={handleStop}
          disabled={false}
        />
        <ActivityTimeline groups={activityGroups} isStreaming={isStreaming} />
      </div>
    </aside>
  );
}

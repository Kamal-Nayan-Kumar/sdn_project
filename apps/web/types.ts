/**
 * Frontend domain types.
 *
 * This file is the TypeScript mirror of
 * `services/api/app/schemas/domain.py` and
 * `services/api/app/agents/state.py`. The Python models are the single source
 * of truth for what the network looks like; when they change, change this file
 * too. Field names deliberately keep the backend's snake_case spelling so that
 * a JSON payload can be dropped straight into these types without a mapping
 * layer.
 *
 * Conventions used here:
 *   - Pydantic `datetime` fields arrive over JSON as ISO-8601 strings.
 *   - Pydantic `str` enums arrive as their `.value`, so they are modelled as
 *     string literal unions rather than TS `enum`s.
 *   - Everything is `readonly`-friendly plain data. There are no class methods:
 *     the UI formats, it does not compute policy.
 *
 * Import it as `@/types` (root alias, resolves to `apps/web/types.ts`) or, from
 * a sibling module, as `../types`.
 */

/* ---------------------------------------------------------------------------
 * Primitives
 * ------------------------------------------------------------------------- */

/** ISO-8601 timestamp, e.g. `2026-10-14T09:31:02.481Z`. */
export type IsoDateTime = string;

/** JSON object with unknown value shapes (Pydantic `dict[str, Any]`). */
export type JsonRecord = Record<string, unknown>;

/* ---------------------------------------------------------------------------
 * Topology
 * ------------------------------------------------------------------------- */

export type NodeKind = "host" | "switch" | "controller";

export type LinkStatus = "up" | "down" | "degraded";

/** `app.schemas.domain.TopologyNode` */
export interface TopologyNode {
  /** Mininet / Ryu node id, e.g. `h1` or `s1`. */
  id: string;
  kind: NodeKind;
  label: string;
  /** Layout hints in SVG coordinates. `null` means "agent should place it". */
  x: number | null;
  y: number | null;
  ip: string | null;
  mac: string | null;
  extra: JsonRecord;
}

/** `app.schemas.domain.TopologyLink` */
export interface TopologyLink {
  /** Stable id, e.g. `s1-s2`. */
  id: string;
  source: string;
  target: string;
  capacity_mbps: number;
  delay_ms: number;
  status: LinkStatus;
  /** True when a controlled congestion generator is attached to this link. */
  congestible: boolean;
}

/** `app.schemas.domain.Topology` */
export interface Topology {
  nodes: TopologyNode[];
  links: TopologyLink[];
  controller: string | null;
  captured_at: IsoDateTime;
}

/* ---------------------------------------------------------------------------
 * Telemetry
 * ------------------------------------------------------------------------- */

/** `app.schemas.domain.LinkTelemetry` */
export interface LinkTelemetry {
  link_id: string;
  /** 0-100. */
  utilization_pct: number;
  capacity_mbps: number;
  tx_bps: number;
  rx_bps: number;
  latency_ms: number | null;
  status: LinkStatus;
  measured_at: IsoDateTime;
}

export type FlowKind = "video" | "voice" | "web" | "background" | "other";

/** `app.schemas.domain.FlowTelemetry` */
export interface FlowTelemetry {
  flow_id: string;
  src_host: string;
  dst_host: string;
  kind: FlowKind;
  /** Current forwarding path as node ids: `["h1", "s1", "s2", "s4", "h4"]`. */
  path: string[];
  latency_ms: number;
  jitter_ms: number | null;
  packet_loss_pct: number;
  throughput_mbps: number | null;
  measured_at: IsoDateTime;
}

/** `app.schemas.domain.ProtectedFlowStatus` */
export interface ProtectedFlowStatus {
  flow_id: string;
  label: string;
  latency_ms: number | null;
  packet_loss_pct: number | null;
  max_latency_ms: number | null;
  max_packet_loss_pct: number | null;
  ok: boolean;
  reason: string | null;
  path_unmodified: boolean;
}

/** `app.schemas.domain.NetworkSnapshot` — one coherent view of the lab. */
export interface NetworkSnapshot {
  topology: Topology;
  links: LinkTelemetry[];
  flows: FlowTelemetry[];
  /** Link ids currently carrying a controlled congestion generator. */
  congested_links: string[];
  active_scenario: string | null;
  controller_reachable: boolean;
  captured_at: IsoDateTime;
}

/* ---------------------------------------------------------------------------
 * Paths
 * ------------------------------------------------------------------------- */

/** `app.schemas.domain.PathCandidate` */
export interface PathCandidate {
  nodes: string[];
  links: string[];
  total_delay_ms: number;
  min_capacity_mbps: number | null;
  max_utilization_pct: number;
  /** True when the path avoids every currently congested link. */
  avoids_congestion: boolean;
  /** True when the path avoids every link carrying a protected flow. */
  avoids_protected: boolean;
  disjoint_from: string[] | null;
}

/** `app.schemas.domain.PathEvaluationResult` */
export interface PathEvaluationResult {
  src: string;
  dst: string;
  candidates: PathCandidate[];
  best_candidate_index: number | null;
  notes: string[];
}

/* ---------------------------------------------------------------------------
 * Intents
 * ------------------------------------------------------------------------- */

/** `app.schemas.domain.ProtectedFlowSpec` */
export interface ProtectedFlowSpec {
  flow_id: string;
  label: string;
  src_host: string | null;
  dst_host: string | null;
  max_latency_ms: number | null;
  max_packet_loss_pct: number | null;
}

/** `app.schemas.domain.IntentSpec`, e.g. "video h1 -> h4 under 50 ms". */
export interface IntentSpec {
  name: string;
  traffic_class: FlowKind;
  src_host: string;
  dst_host: string;
  max_latency_ms: number;
  max_packet_loss_pct: number | null;
  protected_flows: ProtectedFlowSpec[];
  policy: JsonRecord;
}

/* ---------------------------------------------------------------------------
 * Actions and safety
 * ------------------------------------------------------------------------- */

export type ActionType = "reroute_flow" | "reset_lab" | "observe" | "evaluate_paths";

export type RiskLevel = "low" | "medium" | "high";

/** `app.schemas.domain.RerouteAction` — the only network mutation allowed. */
export interface RerouteAction {
  action_type: "reroute_flow";
  flow_id: string;
  src_host: string;
  dst_host: string;
  /** Ordered node ids forming the new path, hosts included. */
  path: string[];
  priority: number;
  reason: string;
  created_at: IsoDateTime;
}

/** `app.schemas.domain.SafetyCheck` */
export interface SafetyCheck {
  name: string;
  passed: boolean;
  detail: string;
  /** The exact numbers the check was based on. Never invented. */
  evidence: JsonRecord;
}

/** `app.schemas.domain.ValidationResult` — Gate 2, deterministic. */
export interface ValidationResult {
  approved: boolean;
  checks: SafetyCheck[];
  rejection_reason: string | null;
  risk_level: RiskLevel;
  requires_approval: boolean;
  validated_at: IsoDateTime;
}

/** `app.schemas.domain.ExecutionResult` */
export interface ExecutionResult {
  action_id: string;
  action_type: ActionType;
  status: "executed" | "failed" | "skipped_awaiting_approval" | "skipped_lab_offline";
  controller_response: JsonRecord;
  flow_rules_installed: number;
  error: string | null;
  executed_at: IsoDateTime;
}

/* ---------------------------------------------------------------------------
 * Evidence and verification
 * ------------------------------------------------------------------------- */

export type EvidenceStatus = "sufficient" | "insufficient" | "stale" | "unavailable";

export type VerificationStatus = "success" | "partial" | "failed" | "unverified";

/** `app.schemas.domain.EvidenceItem` */
export interface EvidenceItem {
  key: string;
  present: boolean;
  fresh: boolean;
  value: unknown;
  source: string;
  measured_at: IsoDateTime | null;
  note: string;
  evidence_extra: JsonRecord;
}

/** `app.schemas.domain.EvidenceAssessment` — Gate 1, deterministic. */
export interface EvidenceAssessment {
  status: EvidenceStatus;
  sufficient: boolean;
  items: EvidenceItem[];
  /** 0-1. */
  confidence: number;
  summary: string;
  missing: string[];
  stale: string[];
  assessed_at: IsoDateTime;
}

/** `app.schemas.domain.VerificationOutcome` — Gate 3, measured, not claimed. */
export interface VerificationOutcome {
  status: VerificationStatus;
  intent_satisfied: boolean;
  protected_ok: boolean;
  before: FlowTelemetry | null;
  after: FlowTelemetry | null;
  protected_flows: ProtectedFlowStatus[];
  reason: string;
  measured_at: IsoDateTime;
}

/* ---------------------------------------------------------------------------
 * Agent run state (app/agents/state.py)
 * ------------------------------------------------------------------------- */

/** `app.agents.state.ToolCallRecord` — one observable tool invocation. */
export interface ToolCallRecord {
  tool: string;
  status: "started" | "success" | "error" | "skipped";
  inputs: JsonRecord;
  outputs: JsonRecord | null;
  duration_ms: number | null;
  error: string | null;
  started_at: IsoDateTime;
  finished_at: IsoDateTime | null;
}

/** Terminal outcome of a run (`AgentState.final_outcome`). */
export type RunOutcome =
  | "intent_satisfied"
  | "partial_improvement"
  | "recovery_failed"
  | "unverified"
  | "abstained_insufficient_evidence"
  | "action_rejected"
  | "cancelled"
  | "lab_offline"
  | "llm_unavailable"
  | "error";

/**
 * High-level UI state of the agent. Distinct from `RunOutcome` because the
 * interface must also express "nothing has been asked yet" and "the run is
 * still going".
 */
export type AgentRunStatus =
  | "idle"
  | "waiting_for_clarification"
  | "running"
  | "gathering_evidence"
  | "insufficient_evidence"
  | "action_proposed"
  | "awaiting_approval"
  | "executing"
  | "verifying"
  | "complete"
  | "failed"
  | "cancelled";

/** The step keys the activity timeline groups by (spec section 9). */
export type ActivityStepKey =
  | "intent_detection"
  | "evidence_collection"
  | "diagnosis_summary"
  | "recovery_planning"
  | "tool_calls"
  | "safety_validation"
  | "execution"
  | "recovery_verification";

/**
 * The subset of `AgentState` the dashboard renders. The backend returns more;
 * the UI only asks for what it draws.
 */
export interface AgentRunSummary {
  run_id: string;
  conversation_id: string | null;
  status: AgentRunStatus;
  outcome: RunOutcome | null;
  user_message: string;
  diagnosis_summary: string | null;
  evidence: EvidenceAssessment | null;
  validation: ValidationResult | null;
  execution: ExecutionResult | null;
  verification: VerificationOutcome | null;
  tool_calls: ToolCallRecord[];
  alternatives_considered: JsonRecord[];
  explanation: string;
  started_at: IsoDateTime;
  finished_at: IsoDateTime | null;
}

/* ---------------------------------------------------------------------------
 * Chat
 * ------------------------------------------------------------------------- */

export type ChatMessageRole = "user" | "assistant" | "system";

/**
 * A structured attachment on an assistant message. The sidebar renders these
 * from the run record; it never manufactures one.
 */
export type ChatAttachment =
  | { kind: "evidence"; data: EvidenceAssessment }
  | { kind: "validation"; data: ValidationResult }
  | { kind: "verification"; data: VerificationOutcome }
  | { kind: "execution"; data: ExecutionResult }
  | { kind: "tool_call"; data: ToolCallRecord };

export interface ChatMessage {
  id: string;
  role: ChatMessageRole;
  content: string;
  createdAt: IsoDateTime;
  /** True while the message is still being streamed. */
  streaming: boolean;
  /** Set on assistant messages produced by a run. */
  runId: string | null;
  outcome: RunOutcome | null;
  attachments: ChatAttachment[];
}

/** `GET /api/conversations` */
export interface ConversationSummary {
  id: string;
  title: string;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
  message_count: number;
}

/* ---------------------------------------------------------------------------
 * Lab / connection status
 * ------------------------------------------------------------------------- */

/** Mirror of the `/health` payload in `services/api/app/main.py`. */
export interface LabHealth {
  status: "ok" | "degraded";
  degraded: boolean;
  lab_mode: string | null;
  database: { ok: boolean; detail: string; backend: string };
  lab: { reachable: boolean; error?: string; mode?: string };
  llm: { configured: boolean; model: string | null };
  safety: {
    require_human_approval_for_risky_actions: boolean;
    evidence_min_confidence: number;
    congestion_utilization_threshold: number;
  };
}

/**
 * Connection state of the frontend -> backend -> lab chain. `offline` and
 * `degraded` are deliberately different states and are never collapsed.
 */
export type ConnectionState = "connecting" | "connected" | "degraded" | "offline";

/* ---------------------------------------------------------------------------
 * SSE events (spec section 10)
 * ------------------------------------------------------------------------- */

interface SseBase {
  run_id: string;
  ts: IsoDateTime;
  /** Monotonic per-run counter used to drop duplicates after a reconnect. */
  seq?: number;
}

export type SseEvent =
  | (SseBase & {
      type: "run_started";
      conversation_id?: string | null;
      trigger?: "reactive" | "proactive";
      user_message?: string;
    })
  | (SseBase & { type: "step_started"; step: ActivityStepKey })
  | (SseBase & { type: "step_completed"; step: ActivityStepKey })
  | (SseBase & { type: "tool_started"; tool: string; inputs?: JsonRecord })
  | (SseBase & {
      type: "tool_completed";
      tool: string;
      status: "success" | "error" | "skipped";
      duration_ms?: number;
      outputs?: JsonRecord | null;
      error?: string | null;
    })
  | (SseBase & {
      type: "evidence_assessed";
      status: EvidenceStatus;
      sufficient: boolean;
      confidence: number;
      summary: string;
    })
  | (SseBase & {
      type: "validation_completed";
      status: "approved" | "rejected";
      checks_passed: number;
      checks_failed: number;
      risk_level: RiskLevel;
      rejection_reason?: string | null;
    })
  | (SseBase & {
      type: "verification_completed";
      status: VerificationStatus;
      intent_satisfied: boolean;
      latency_ms?: number | null;
      reason?: string;
    })
  /** Incremental assistant text. */
  | (SseBase & { type: "token"; text: string })
  | (SseBase & { type: "run_completed"; outcome: RunOutcome; explanation?: string })
  | (SseBase & { type: "run_cancelled"; reason?: string })
  | (SseBase & { type: "error"; message: string; stage?: string });

/* ---------------------------------------------------------------------------
 * Component contracts for the parallel agent
 *
 * The dashboard shell renders typed slots. Each interface below is the exact
 * prop contract for the component the other agent must supply. If a prop is
 * optional it has a sensible default and the slot passes nothing.
 * ------------------------------------------------------------------------- */

/**
 * `src/components/topology/TopologyCanvas.tsx` (default or named export
 * `TopologyCanvas`).
 *
 * Draws hosts, switches and links from real snapshot state: link utilisation,
 * congested/failed links, the current path per flow and any candidate path.
 * SVG, not images. Must render an honest empty state when `snapshot` is null.
 */
export interface TopologyCanvasProps {
  /** Live snapshot. `null` while the first fetch is in flight or the lab is down. */
  snapshot: NetworkSnapshot | null;
  /** Node-id path per flow, keyed by flow id. */
  flowPaths?: Record<string, string[]>;
  /** Candidate path under consideration (drawn as the alternative route). */
  alternativePath?: string[] | null;
  /** Path currently selected / executed by the agent. */
  selectedPath?: string[] | null;
  /** Link ids the agent flagged as suspect bottlenecks. */
  suspectLinks?: string[];
  /** Link ids carrying a controlled congestion generator. */
  congestedLinks?: string[];
  /** Flow highlighted by the shell (row hover in the flow table). */
  selectedFlowId?: string | null;
  onSelectNode?: (nodeId: string | null) => void;
  onSelectLink?: (linkId: string | null) => void;
  /** Canonical canvas height in px. Default 420. */
  height?: number;
  className?: string;
}

/**
 * `src/components/chat/ChatPanel.tsx` (named export `ChatPanel`).
 *
 * Conversation history, user/assistant messages, streaming text, message input
 * and the stop/cancel control.
 */
export interface ChatPanelProps {
  conversationId: string | null;
  messages: ChatMessage[];
  agentStatus: AgentRunStatus;
  /** True while a run is streaming tokens. */
  isStreaming: boolean;
  onSend: (text: string) => void | Promise<void>;
  onStop?: () => void;
  /** True when the input must be disabled because the backend is unreachable. */
  disabled?: boolean;
  className?: string;
}

/** One expandable group in the activity timeline. */
export interface ActivityGroup {
  key: ActivityStepKey;
  label: string;
  status: "pending" | "active" | "complete" | "failed" | "skipped";
  /** Concise reasoning summary. Never raw chain-of-thought. */
  summary?: string;
  startedAt?: IsoDateTime | null;
  finishedAt?: IsoDateTime | null;
  toolCalls?: ToolCallRecord[];
  /** Structured findings for this group. */
  findings?: ChatAttachment[];
  /** Nested sub-steps, e.g. individual tool calls. */
  children?: ActivityGroup[];
}

/**
 * `src/components/agent-activity/ActivityTimeline.tsx` (named export
 * `ActivityTimeline`).
 */
export interface ActivityTimelineProps {
  groups: ActivityGroup[];
  /** Currently expanded group keys. Omit for internal uncontrolled state. */
  expandedKeys?: string[];
  onToggleGroup?: (key: string) => void;
  /** True while a run is live, for the "Live" affordance. */
  isStreaming?: boolean;
  className?: string;
}

/**
 * `src/components/metrics/MetricsPanel.tsx` (named export `MetricsPanel`).
 *
 * Before/after latency and packet loss, link utilisation, protected-flow
 * performance, recovery time, intent status and the validation/action history.
 */
export interface MetricsPanelProps {
  snapshot: NetworkSnapshot | null;
  intent: IntentSpec | null;
  run: AgentRunSummary | null;
  /** Previous run outcomes, newest first, for the audit strip. */
  history?: AgentRunSummary[];
  className?: string;
}

/** `src/components/metrics/MetricStrip.tsx` (named export `MetricStrip`). */
export interface MetricStripProps {
  snapshot: NetworkSnapshot | null;
  intent: IntentSpec | null;
  verification: VerificationOutcome | null;
  className?: string;
}

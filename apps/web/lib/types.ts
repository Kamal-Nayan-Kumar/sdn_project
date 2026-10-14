/**
 * Domain + UI types for the network-operations dashboard.
 *
 * Every type here mirrors a Pydantic model that actually exists:
 *   * `services/api/app/schemas/domain.py`  (Topology, NetworkSnapshot, ...)
 *   * `services/api/app/agents/state.py`    (ToolCallRecord, AgentState)
 *   * `services/api/app/tools/__init__.py`  (tool allow-list names)
 *
 * These live under `apps/web/lib/` on purpose: the presentational layer
 * (`apps/web/components/**`) has no dependency on anything outside
 * `apps/web/components/**` + `apps/web/lib/**`, so it stays buildable and
 * testable on its own. If `apps/web/types.ts` is added by the app scaffold it
 * should simply re-export from here so the two can never drift:
 *
 *     export * from "@/lib/types";
 *
 * Two shapes are deliberately *permissive* because the lab and the API do not
 * always agree:
 *   1. `NetworkSnapshot.links` is an array when it comes from FastAPI, but the
 *      lab serves it as an object keyed by `link_id`. Always normalise with
 *      `linksArray()` from `./derive`.
 *   2. Optional fields mirror Pydantic defaults; a field that the Python model
 *      always populates is required here, everything else is optional.
 */

/* -------------------------------------------------------------------------- */
/* Topology                                                                    */
/* -------------------------------------------------------------------------- */

export type NodeKind = "host" | "switch" | "controller";

export type LinkStatus = "up" | "down" | "degraded";

export interface TopologyNode {
  readonly id: string;
  readonly kind?: NodeKind;
  readonly label?: string;
  /** Optional SVG position hint from the backend. */
  readonly x?: number | null;
  readonly y?: number | null;
  readonly ip?: string | null;
  readonly mac?: string | null;
  /** Presentation extras the lab adds (`role`, `port`, ...). */
  readonly extra?: Readonly<Record<string, unknown>> | null;
}

export interface TopologyLink {
  /** Stable edge id, e.g. `s1-s2` (sorted endpoint ids joined with `-`). */
  readonly id: string;
  readonly source: string;
  readonly target: string;
  readonly capacity_mbps?: number;
  readonly delay_ms?: number;
  readonly status?: LinkStatus;
  /** True when a controlled congestion generator is attached to this link. */
  readonly congestible?: boolean;
  /** The lab tags host access edges with `kind: "access"`. */
  readonly kind?: string;
  readonly extra?: Readonly<Record<string, unknown>> | null;
}

export interface Topology {
  readonly nodes: readonly TopologyNode[];
  readonly links: readonly TopologyLink[];
  readonly controller?: string | null;
  readonly captured_at?: string;
}

/** An empty topology, used as a defensive fallback. */
export const EMPTY_TOPOLOGY: Topology = { nodes: [], links: [], controller: null };

/* -------------------------------------------------------------------------- */
/* Telemetry                                                                   */
/* -------------------------------------------------------------------------- */

export interface LinkTelemetry {
  readonly link_id: string;
  readonly utilization_pct: number;
  readonly capacity_mbps: number;
  readonly tx_bps?: number;
  readonly rx_bps?: number;
  readonly latency_ms?: number | null;
  readonly status?: LinkStatus;
  readonly measured_at?: string;
}

export type FlowKind = "video" | "voice" | "web" | "background" | "other";

export interface FlowTelemetry {
  readonly flow_id: string;
  readonly src_host: string;
  readonly dst_host: string;
  readonly kind?: FlowKind;
  /** Full node path, e.g. `["h1", "s1", "s2", "s4", "h4"]`. */
  readonly path?: readonly string[];
  readonly latency_ms: number;
  readonly jitter_ms?: number | null;
  readonly packet_loss_pct?: number;
  readonly throughput_mbps?: number | null;
  readonly measured_at?: string;
}

export interface ProtectedFlowStatus {
  readonly flow_id: string;
  readonly label?: string;
  readonly latency_ms?: number | null;
  readonly packet_loss_pct?: number | null;
  readonly max_latency_ms?: number | null;
  readonly max_packet_loss_pct?: number | null;
  readonly ok?: boolean;
  readonly reason?: string | null;
  readonly path_unmodified?: boolean;
}

/**
 * `NetworkSnapshot.links` as it actually arrives on the wire: a list from
 * FastAPI, an object keyed by `link_id` from the lab.
 */
export type LinksPayload =
  | readonly LinkTelemetry[]
  | Readonly<Record<string, LinkTelemetry>>
  | null
  | undefined;

export interface NetworkSnapshot {
  readonly topology?: Topology | null;
  readonly links?: LinksPayload;
  readonly flows?: readonly FlowTelemetry[] | null;
  /** Link ids currently carrying a controlled congestion generator. */
  readonly congested_links?: readonly string[] | null;
  readonly active_scenario?: string | null;
  readonly controller_reachable?: boolean;
  readonly captured_at?: string;
}

/* -------------------------------------------------------------------------- */
/* Paths                                                                       */
/* -------------------------------------------------------------------------- */

export interface PathCandidate {
  readonly nodes?: readonly string[];
  readonly links?: readonly string[];
  readonly total_delay_ms?: number;
  readonly min_capacity_mbps?: number | null;
  readonly max_utilization_pct?: number;
  readonly avoids_congestion?: boolean;
  readonly avoids_protected?: boolean;
  readonly disjoint_from?: readonly string[] | null;
}

/* -------------------------------------------------------------------------- */
/* Intents                                                                     */
/* -------------------------------------------------------------------------- */

export interface ProtectedFlowSpec {
  readonly flow_id: string;
  readonly label?: string;
  readonly src_host?: string | null;
  readonly dst_host?: string | null;
  readonly max_latency_ms?: number | null;
  readonly max_packet_loss_pct?: number | null;
}

export interface IntentSpec {
  readonly name?: string;
  readonly traffic_class?: FlowKind;
  readonly src_host: string;
  readonly dst_host: string;
  readonly max_latency_ms: number;
  readonly max_packet_loss_pct?: number | null;
  readonly protected_flows?: readonly ProtectedFlowSpec[];
  readonly policy?: Readonly<Record<string, unknown>>;
}

/* -------------------------------------------------------------------------- */
/* Actions, safety, execution                                                  */
/* -------------------------------------------------------------------------- */

export type RiskLevel = "low" | "medium" | "high";

export interface RerouteAction {
  readonly action_type?: "reroute_flow";
  readonly flow_id: string;
  readonly src_host: string;
  readonly dst_host: string;
  /** Ordered node ids, hosts included: `[h1, s1, s3, s4, h4]`. */
  readonly path: readonly string[];
  readonly priority?: number;
  readonly reason?: string;
  readonly created_at?: string;
}

export interface SafetyCheck {
  readonly name: string;
  readonly passed: boolean;
  readonly detail?: string;
  readonly evidence?: Readonly<Record<string, unknown>>;
}

export interface ValidationResult {
  readonly approved: boolean;
  readonly checks?: readonly SafetyCheck[];
  readonly rejection_reason?: string | null;
  readonly risk_level?: RiskLevel;
  readonly requires_approval?: boolean;
  readonly validated_at?: string;
}

export type ExecutionStatus =
  | "executed"
  | "failed"
  | "skipped_awaiting_approval"
  | "skipped_lab_offline";

export interface ExecutionResult {
  readonly action_id: string;
  readonly action_type?: string;
  readonly status: ExecutionStatus;
  readonly controller_response?: Readonly<Record<string, unknown>>;
  readonly flow_rules_installed?: number;
  readonly error?: string | null;
  readonly executed_at?: string;
}

/* -------------------------------------------------------------------------- */
/* Evidence + verification                                                     */
/* -------------------------------------------------------------------------- */

export type EvidenceStatus = "sufficient" | "insufficient" | "stale" | "unavailable";

export interface EvidenceItem {
  readonly key: string;
  readonly present: boolean;
  readonly fresh?: boolean;
  readonly value?: unknown;
  readonly source?: string;
  readonly measured_at?: string | null;
  readonly note?: string;
  readonly evidence_extra?: Readonly<Record<string, unknown>>;
}

export interface EvidenceAssessment {
  readonly status: EvidenceStatus;
  readonly sufficient: boolean;
  readonly items?: readonly EvidenceItem[];
  readonly confidence?: number;
  readonly summary?: string;
  readonly missing?: readonly string[];
  readonly stale?: readonly string[];
  readonly assessed_at?: string;
}

export type VerificationStatus = "success" | "partial" | "failed" | "unverified";

export interface VerificationOutcome {
  readonly status: VerificationStatus;
  readonly intent_satisfied?: boolean;
  readonly protected_ok?: boolean;
  readonly before?: FlowTelemetry | null;
  readonly after?: FlowTelemetry | null;
  readonly protected_flows?: readonly ProtectedFlowStatus[];
  readonly reason?: string;
  readonly measured_at?: string;
}

/** Tool-call status, matching `ToolCallRecord.status` in state.py. */
export type ToolCallStatus = "started" | "success" | "error" | "skipped";

export interface ToolCallRecord {
  readonly tool: string;
  readonly status: ToolCallStatus;
  readonly inputs?: Readonly<Record<string, unknown>>;
  readonly outputs?: Readonly<Record<string, unknown>> | null;
  readonly duration_ms?: number | null;
  readonly error?: string | null;
  readonly started_at?: string;
  readonly finished_at?: string | null;
}

export type FinalOutcome =
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

/* -------------------------------------------------------------------------- */
/* Agent run activity groups (spec section 9)                                  */
/* -------------------------------------------------------------------------- */

/** The eight activity groups the dashboard must show, in display order. */
export const ACTIVITY_GROUP_KEYS = [
  "intent_detection",
  "evidence_collection",
  "diagnosis_summary",
  "recovery_planning",
  "tool_calls",
  "safety_validation",
  "execution",
  "recovery_verification",
] as const;

export type ActivityGroupKey = (typeof ACTIVITY_GROUP_KEYS)[number];

/** Extra buckets that are not one of the eight named groups. */
export type ExtraGroupKey = "run" | "notes";

export const ACTIVITY_GROUP_LABELS: Readonly<Record<ActivityGroupKey, string>> = {
  intent_detection: "Intent detection",
  evidence_collection: "Evidence collection",
  diagnosis_summary: "Diagnosis summary",
  recovery_planning: "Recovery planning",
  tool_calls: "Tool calls",
  safety_validation: "Safety validation",
  execution: "Execution",
  recovery_verification: "Recovery verification",
};

export type ActivityGroupStatus = "pending" | "running" | "complete" | "partial" | "failed";

export interface ActivityGroup {
  readonly key: ActivityGroupKey | ExtraGroupKey;
  readonly label: string;
  /** True for one of the eight groups named in the spec. */
  readonly core: boolean;
  readonly status: ActivityGroupStatus;
  readonly events: readonly AgentRunEvent[];
  readonly summary: string;
  readonly startedAt: string | null;
  readonly finishedAt: string | null;
}

/* -------------------------------------------------------------------------- */
/* SSE event contract (spec section 10)                                        */
/* -------------------------------------------------------------------------- */

/**
 * Every event carries a run id, a UTC timestamp and a sequence number so the
 * frontend can dedupe and order events that arrive out of order or twice
 * (which is exactly what happens on an SSE reconnect).
 */
interface AgentRunEventBase {
  readonly run_id: string;
  /** Monotonic per run. `0` when the server did not send one. */
  readonly seq: number;
  /** ISO-8601 UTC timestamp. */
  readonly ts: string;
  /** Which activity group this event belongs to. */
  readonly group: ActivityGroupKey | ExtraGroupKey;
}

export interface RunStartedEvent extends AgentRunEventBase {
  readonly type: "run_started";
  readonly conversation_id?: string | null;
  readonly trigger?: "reactive" | "proactive";
  readonly user_message?: string;
}

export interface StepStartedEvent extends AgentRunEventBase {
  readonly type: "step_started";
  readonly step: string;
  readonly label?: string;
}

export interface StepCompletedEvent extends AgentRunEventBase {
  readonly type: "step_completed";
  readonly step: string;
  readonly status?: "ok" | "failed" | "skipped";
  readonly summary?: string;
}

export interface IntentDetectedEvent extends AgentRunEventBase {
  readonly type: "intent_detected";
  readonly intent?: IntentSpec | null;
  readonly needs_clarification?: boolean;
  readonly clarification_question?: string | null;
  readonly summary?: string;
}

export interface ClarificationRequestedEvent extends AgentRunEventBase {
  readonly type: "clarification_requested";
  readonly question: string;
}

export interface ToolStartedEvent extends AgentRunEventBase {
  readonly type: "tool_started";
  readonly tool: string;
  readonly inputs?: Readonly<Record<string, unknown>>;
}

export interface ToolCompletedEvent extends AgentRunEventBase {
  readonly type: "tool_completed";
  readonly tool: string;
  readonly status?: "success" | "error" | "skipped";
  readonly duration_ms?: number | null;
  readonly outputs?: Readonly<Record<string, unknown>> | null;
  readonly error?: string | null;
}

export interface ToolFailedEvent extends AgentRunEventBase {
  readonly type: "tool_failed";
  readonly tool: string;
  readonly error: string;
  readonly duration_ms?: number | null;
}

export interface EvidenceAssessedEvent extends AgentRunEventBase {
  readonly type: "evidence_assessed";
  readonly assessment: EvidenceAssessment;
}

export interface DiagnosisCompletedEvent extends AgentRunEventBase {
  readonly type: "diagnosis_completed";
  readonly summary: string;
  readonly suspect_links?: readonly string[];
}

export interface PlanProposedEvent extends AgentRunEventBase {
  readonly type: "plan_proposed";
  readonly action?: RerouteAction | null;
  readonly alternatives?: readonly Record<string, unknown>[];
  readonly notes?: readonly string[];
}

export interface PathEvaluatedEvent extends AgentRunEventBase {
  readonly type: "path_evaluated";
  readonly src?: string;
  readonly dst?: string;
  readonly candidates: readonly PathCandidate[];
  readonly best_index?: number | null;
  readonly notes?: readonly string[];
}

export interface ValidationCompletedEvent extends AgentRunEventBase {
  readonly type: "validation_completed";
  readonly result: ValidationResult;
}

export interface ApprovalRequestedEvent extends AgentRunEventBase {
  readonly type: "approval_requested";
  readonly action_id?: string;
  readonly risk_level?: RiskLevel;
  readonly reason?: string;
}

export interface ApprovalDecidedEvent extends AgentRunEventBase {
  readonly type: "approval_decided";
  readonly approved: boolean;
  readonly decided_by?: string;
  readonly note?: string;
}

export interface ExecutionStartedEvent extends AgentRunEventBase {
  readonly type: "execution_started";
  readonly action_id?: string;
  readonly tool?: string;
}

export interface ExecutionCompletedEvent extends AgentRunEventBase {
  readonly type: "execution_completed";
  readonly result: ExecutionResult;
}

export interface VerificationCompletedEvent extends AgentRunEventBase {
  readonly type: "verification_completed";
  readonly outcome: VerificationOutcome;
}

export interface NoteEvent extends AgentRunEventBase {
  readonly type: "note";
  readonly level?: "info" | "warn" | "error";
  readonly message: string;
}

export interface MessageDeltaEvent extends AgentRunEventBase {
  readonly type: "message_delta";
  readonly delta: string;
}

export interface RunCompletedEvent extends AgentRunEventBase {
  readonly type: "run_completed";
  readonly outcome: FinalOutcome;
  readonly message?: string;
}

export interface RunFailedEvent extends AgentRunEventBase {
  readonly type: "run_failed";
  readonly error: string;
  readonly outcome?: FinalOutcome;
}

export interface RunCancelledEvent extends AgentRunEventBase {
  readonly type: "run_cancelled";
  readonly reason?: string;
}

export type AgentRunEvent =
  | RunStartedEvent
  | StepStartedEvent
  | StepCompletedEvent
  | IntentDetectedEvent
  | ClarificationRequestedEvent
  | ToolStartedEvent
  | ToolCompletedEvent
  | ToolFailedEvent
  | EvidenceAssessedEvent
  | DiagnosisCompletedEvent
  | PlanProposedEvent
  | PathEvaluatedEvent
  | ValidationCompletedEvent
  | ApprovalRequestedEvent
  | ApprovalDecidedEvent
  | ExecutionStartedEvent
  | ExecutionCompletedEvent
  | VerificationCompletedEvent
  | NoteEvent
  | MessageDeltaEvent
  | RunCompletedEvent
  | RunFailedEvent
  | RunCancelledEvent;

export type AgentRunEventType = AgentRunEvent["type"];

/** The terminal events. The stream must be closed after one of these. */
export const TERMINAL_EVENT_TYPES: readonly AgentRunEventType[] = [
  "run_completed",
  "run_failed",
  "run_cancelled",
];

/** SSE frame names we listen for. Servers may use either the default
 *  `message` frame or a named frame per event type. */
export const SSE_EVENT_NAMES: readonly string[] = [
  "message",
  "run_started",
  "step_started",
  "step_completed",
  "intent_detected",
  "clarification_requested",
  "tool_started",
  "tool_completed",
  "tool_failed",
  "evidence_assessed",
  "diagnosis_completed",
  "plan_proposed",
  "path_evaluated",
  "validation_completed",
  "approval_requested",
  "approval_decided",
  "execution_started",
  "execution_completed",
  "verification_completed",
  "note",
  "message_delta",
  "run_completed",
  "run_failed",
  "run_cancelled",
];

/* -------------------------------------------------------------------------- */
/* API payloads (services/api/app/services/lab_bridge.py + network-lab)        */
/* -------------------------------------------------------------------------- */

export interface HealthResponse {
  readonly status?: string;
  readonly ok?: boolean;
  readonly service?: string;
  readonly version?: string;
  readonly time?: string;
  readonly [key: string]: unknown;
}

export interface LabStatusResponse {
  readonly running?: boolean;
  readonly switch_backend?: string | null;
  readonly controller_reachable?: boolean;
  readonly connected_switches?: number;
  readonly congested_links?: readonly string[];
  readonly uptime_seconds?: number;
  readonly last_utilization?: Readonly<Record<string, number | null>>;
  readonly [key: string]: unknown;
}

export interface TopologyResponse {
  readonly nodes?: readonly TopologyNode[];
  readonly links?: readonly TopologyLink[];
  readonly controller?: string | null;
  readonly switch_backend?: string | null;
  readonly captured_at?: string | number;
  readonly [key: string]: unknown;
}

export interface SnapshotResponse {
  readonly topology?: Topology | null;
  readonly links?: LinksPayload;
  readonly flows?: readonly FlowTelemetry[] | null;
  readonly congested_links?: readonly string[] | null;
  readonly active_scenario?: string | null;
  readonly controller_reachable?: boolean;
  readonly measured_at?: number;
  readonly captured_at?: string | number;
  readonly switch_backend?: string | null;
  readonly [key: string]: unknown;
}

export interface PathsResponse {
  readonly paths?: readonly RawPath[];
  readonly candidates?: readonly PathCandidate[];
  readonly src?: string;
  readonly dst?: string;
  readonly avoided_links?: readonly string[];
  readonly [key: string]: unknown;
}

/** A path as the lab reports it (`/v1/paths`). */
export interface RawPath {
  readonly nodes?: readonly string[];
  readonly switches?: readonly string[];
  readonly links?: readonly string[];
  readonly total_delay_ms?: number;
  readonly min_capacity_mbps?: number | null;
  readonly [key: string]: unknown;
}

export interface RerouteRequest {
  readonly src_host: string;
  readonly dst_host: string;
  readonly path: readonly string[];
  readonly flow_id?: string;
  readonly priority?: number;
  readonly reason?: string;
}

export interface RerouteResponse {
  readonly ok?: boolean;
  readonly error?: string | null;
  readonly path?: readonly string[];
  readonly controller?: unknown;
  readonly rules_installed?: number;
  readonly [key: string]: unknown;
}

export interface ScenarioResetResponse {
  readonly ok?: boolean;
  readonly error?: string | null;
  readonly scenario?: string | null;
  readonly [key: string]: unknown;
}

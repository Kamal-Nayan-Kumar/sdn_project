/**
 * The agent-run event reducer: pure, dependency free, unit testable.
 *
 * Responsibilities, taken straight from the spec's streaming requirements
 * (section 10):
 *   * every event carries `run_id` + `ts` + `seq`;
 *   * duplicates (a reconnect replays them) are ignored, keyed on
 *     `(run_id, seq)`;
 *   * events may arrive out of order, so they are inserted by `seq`;
 *   * malformed frames are dropped rather than guessed at;
 *   * a terminal event (`run_completed` / `run_failed` / `run_cancelled`)
 *     settles the run and closes the stream.
 *
 * Nothing in this file touches React, `EventSource` or the clock, so it can be
 * imported by the hook *and* by a plain Node assertion script.
 */

import type {
  ActivityGroup,
  ActivityGroupKey,
  ActivityGroupStatus,
  AgentRunEvent,
  AgentRunEventType,
  EvidenceAssessment,
  ExecutionResult,
  FinalOutcome,
  FlowKind,
  FlowTelemetry,
  IntentSpec,
  PathCandidate,
  RerouteAction,
  ToolCallRecord,
  ValidationResult,
  VerificationOutcome,
} from "./types";
import { ACTIVITY_GROUP_KEYS, TERMINAL_EVENT_TYPES } from "./types";
import { groupEvents, toolCallsFromEvents } from "./derive";

export type RunStatus = "idle" | "running" | "waiting_approval" | "completed" | "failed" | "cancelled";

export interface AgentRunState {
  readonly runId: string | null;
  readonly status: RunStatus;
  /** Every accepted event, ordered by `seq`. */
  readonly events: readonly AgentRunEvent[];
  /** `(run_id, seq)` keys already applied -- the dedupe index. */
  readonly seen: Readonly<Record<string, true>>;
  readonly seq: number;
  readonly conversationId: string | null;
  readonly trigger: "reactive" | "proactive" | null;
  readonly userMessage: string;
  readonly intent: IntentSpec | null;
  readonly needsClarification: boolean;
  readonly clarificationQuestion: string | null;
  readonly diagnosisSummary: string;
  readonly suspectLinks: readonly string[];
  readonly plan: RerouteAction | null;
  readonly candidatePaths: readonly PathCandidate[];
  readonly toolCalls: readonly ToolCallRecord[];
  readonly evidence: EvidenceAssessment | null;
  readonly validation: ValidationResult | null;
  readonly execution: ExecutionResult | null;
  readonly verification: VerificationOutcome | null;
  readonly awaitingApproval: boolean;
  readonly approvedBy: string | null;
  readonly outcome: FinalOutcome | null;
  readonly error: string | null;
  readonly cancelled: boolean;
  /** Accumulated `message_delta` text. */
  readonly assistantText: string;
  readonly finalMessage: string | null;
  /** Frames dropped because they could not be parsed. */
  readonly droppedCount: number;
  readonly startedAt: string | null;
  readonly finishedAt: string | null;
}

export const initialAgentRunState: AgentRunState = Object.freeze({
  runId: null,
  status: "idle" as RunStatus,
  events: [] as readonly AgentRunEvent[],
  seen: {} as Readonly<Record<string, true>>,
  seq: 0,
  conversationId: null,
  trigger: null,
  userMessage: "",
  intent: null,
  needsClarification: false,
  clarificationQuestion: null,
  diagnosisSummary: "",
  suspectLinks: [] as readonly string[],
  plan: null,
  candidatePaths: [] as readonly PathCandidate[],
  toolCalls: [] as readonly ToolCallRecord[],
  evidence: null,
  validation: null,
  execution: null,
  verification: null,
  awaitingApproval: false,
  approvedBy: null,
  outcome: null,
  error: null,
  cancelled: false,
  assistantText: "",
  finalMessage: null,
  droppedCount: 0,
  startedAt: null,
  finishedAt: null,
});

/** `false` for anything that is not a usable event object. */
function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

const asString = (value: unknown, fallback: string): string =>
  typeof value === "string" && value.length > 0 ? value : fallback;

const asOptionalString = (value: unknown): string | null =>
  typeof value === "string" && value.length > 0 ? value : null;

const asBoolean = (value: unknown, fallback: boolean): boolean =>
  typeof value === "boolean" ? value : fallback;

function timeToNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Date.parse(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

/** ISO-8601 UTC timestamp for an event that did not carry one. */
function timestampNow(): string {
  return new Date().toISOString();
}

function asNumberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function asStringArray(value: unknown): string[] {
  return asArray(value).filter((v): v is string => typeof v === "string" && v.length > 0);
}

function asObjectOrNull(value: unknown): Record<string, unknown> | null {
  return asRecord(value);
}

/* -------------------------------------------------------------------------- */
/* Frame normalisation                                                        */
/* -------------------------------------------------------------------------- */

const KNOWN_TYPES: ReadonlySet<string> = new Set([
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
]);

const asEventType = (value: unknown): AgentRunEventType | null => {
  if (typeof value !== "string") return null;
  return KNOWN_TYPES.has(value) ? (value as AgentRunEventType) : null;
};

/** Is this event one of the three terminal events (spec section 10)? */
export const isTerminalEvent = (type: AgentRunEventType): boolean =>
  (TERMINAL_EVENT_TYPES as readonly string[]).includes(type);

/** Activity group an event belongs to; mirrors `derive.groupKeyForEvent`. */
function groupOf(raw: Record<string, unknown>, type: AgentRunEventType): ActivityGroupKey | null {
  const step = raw.step;
  if (typeof step === "string" && (ACTIVITY_GROUP_KEYS as readonly string[]).includes(step)) {
    return step as ActivityGroupKey;
  }
  switch (type) {
    case "evidence_assessed":
      return "evidence_collection";
    case "diagnosis_completed":
      return "diagnosis_summary";
    case "plan_proposed":
    case "path_evaluated":
      return "recovery_planning";
    case "tool_started":
    case "tool_completed":
    case "tool_failed":
      return "tool_calls";
    case "validation_completed":
    case "approval_requested":
    case "approval_decided":
      return "safety_validation";
    case "execution_started":
    case "execution_completed":
      return "execution";
    case "verification_completed":
      return "recovery_verification";
    default:
      return null;
  }
}

/**
 * Turn one raw SSE frame (`JSON.parse`d `MessageEvent.data`) into a typed
 * event. Returns `null` when the frame is unusable -- a missing/unknown
 * `type`, or a non-object payload -- so the caller never has to guess.
 *
 * A missing `seq` or `ts` does not invalidate an event: the reducer fills them
 * in, because dropping a real observation would be worse than labelling one.
 */
export function normaliseAgentEvent(raw: unknown, fallbackSeq = 0): AgentRunEvent | null {
  const obj = asRecord(raw);
  if (!obj) return null;
  const type = asEventType(obj.type);
  if (!type) return null;

  const runId = asString(obj.run_id, "");
  const seqRaw = asNumberOrNull(obj.seq);
  const tsRaw = obj.ts;
  const group = groupOf(obj, type);

  const base = {
    run_id: runId,
    seq: seqRaw ?? Math.max(0, Math.floor(fallbackSeq)),
    ts: typeof tsRaw === "string" && tsRaw.length > 0 ? tsRaw : timestampNow(),
    group,
  };

  switch (type) {
    case "run_started":
      return {
        ...base,
        type,
        conversation_id: asOptionalString(obj.conversation_id),
        trigger: obj.trigger === "proactive" || obj.trigger === "reactive" ? obj.trigger : undefined,
        user_message: asOptionalString(obj.user_message) ?? undefined,
      };
    case "step_started":
      return {
        ...base,
        type,
        step: asString(obj.step, ""),
        label: asOptionalString(obj.label) ?? undefined,
      };
    case "step_completed":
      return {
        ...base,
        type,
        step: asString(obj.step, ""),
        status:
          obj.status === "failed" || obj.status === "skipped"
            ? obj.status
            : obj.status === "ok"
              ? "ok"
              : undefined,
        summary: asOptionalString(obj.summary) ?? undefined,
      };
    case "intent_detected": {
      const intent = asObjectOrNull(obj.intent);
      return {
        ...base,
        type,
        intent: intent
          ? {
              name: asString(intent.name, "intent"),
              traffic_class:
                typeof intent.traffic_class === "string"
                  ? (intent.traffic_class as FlowKind)
                  : undefined,
              src_host: asString(intent.src_host, ""),
              dst_host: asString(intent.dst_host, ""),
              max_latency_ms:
                typeof intent.max_latency_ms === "number" ? intent.max_latency_ms : 0,
              max_packet_loss_pct: asNumberOrNull(intent.max_packet_loss_pct),
              protected_flows: asArray(intent.protected_flows).map((p) => {
                const pf = asRecord(p) ?? {};
                return {
                  flow_id: asString(pf.flow_id, ""),
                  label: asOptionalString(pf.label) ?? undefined,
                  src_host: asOptionalString(pf.src_host),
                  dst_host: asOptionalString(pf.dst_host),
                  max_latency_ms: asNumberOrNull(pf.max_latency_ms),
                  max_packet_loss_pct: asNumberOrNull(pf.max_packet_loss_pct),
                };
              }),
            }
          : null,
        needs_clarification: asBoolean(obj.needs_clarification, false),
        clarification_question: asOptionalString(obj.clarification_question),
        summary: asOptionalString(obj.summary) ?? undefined,
      };
    }
    case "clarification_requested":
      return { ...base, type, question: asString(obj.question, "") };
    case "tool_started":
      return {
        ...base,
        type,
        tool: asString(obj.tool, ""),
        inputs: asObjectOrNull(obj.inputs) ?? undefined,
      };
    case "tool_completed":
      return {
        ...base,
        type,
        tool: asString(obj.tool, ""),
        status:
          obj.status === "error" || obj.status === "skipped"
            ? obj.status
            : obj.status === "success"
              ? "success"
              : undefined,
        duration_ms: asNumberOrNull(obj.duration_ms),
        outputs: asObjectOrNull(obj.outputs),
        error: asOptionalString(obj.error),
      };
    case "tool_failed":
      return {
        ...base,
        type,
        tool: asString(obj.tool, ""),
        error: asString(obj.error, "tool failed"),
        duration_ms: asNumberOrNull(obj.duration_ms),
      };
    case "evidence_assessed": {
      const a = asObjectOrNull(obj.assessment) ?? {};
      return {
        ...base,
        type,
        assessment: {
          status:
            a.status === "insufficient" || a.status === "stale" || a.status === "unavailable"
              ? a.status
              : "sufficient",
          sufficient: asBoolean(a.sufficient, a.status === "sufficient"),
          items: asArray(a.items).map((item) => {
            const ei = asRecord(item) ?? {};
            return {
              key: asString(ei.key, ""),
              present: asBoolean(ei.present, false),
              fresh: asBoolean(ei.fresh, true),
              value: ei.value ?? null,
              source: asString(ei.source, ""),
              measured_at: asOptionalString(ei.measured_at),
              note: asString(ei.note, ""),
              evidence_extra: (asObjectOrNull(ei.evidence_extra) ?? {}) as Record<string, unknown>,
            };
          }),
          confidence: typeof a.confidence === "number" ? a.confidence : 0,
          summary: asString(a.summary, ""),
          missing: asStringArray(a.missing),
          stale: asStringArray(a.stale),
          assessed_at: asOptionalString(a.assessed_at) ?? undefined,
        },
      };
    }
    case "diagnosis_completed":
      return {
        ...base,
        type,
        summary: asString(obj.summary, ""),
        suspect_links: asStringArray(obj.suspect_links),
      };
    case "plan_proposed": {
      const a = asObjectOrNull(obj.action);
      return {
        ...base,
        type,
        action: a
          ? {
              flow_id: asString(a.flow_id, ""),
              src_host: asString(a.src_host, ""),
              dst_host: asString(a.dst_host, ""),
              path: asStringArray(a.path),
              priority: typeof a.priority === "number" ? a.priority : undefined,
              reason: asString(a.reason, ""),
              created_at: asOptionalString(a.created_at) ?? undefined,
            }
          : null,
        alternatives: asArray(obj.alternatives) as Record<string, unknown>[],
        notes: asStringArray(obj.notes),
      };
    }
    case "path_evaluated":
      return {
        ...base,
        type,
        src: asOptionalString(obj.src) ?? undefined,
        dst: asOptionalString(obj.dst) ?? undefined,
        candidates: asArray(obj.candidates).map((c) => {
          const cc = asRecord(c) ?? {};
          return {
            nodes: asStringArray(cc.nodes),
            links: asStringArray(cc.links),
            total_delay_ms: typeof cc.total_delay_ms === "number" ? cc.total_delay_ms : 0,
            min_capacity_mbps: asNumberOrNull(cc.min_capacity_mbps),
            max_utilization_pct: typeof cc.max_utilization_pct === "number" ? cc.max_utilization_pct : 0,
            avoids_congestion:
              typeof cc.avoids_congestion === "boolean" ? cc.avoids_congestion : undefined,
            avoids_protected:
              typeof cc.avoids_protected === "boolean" ? cc.avoids_protected : undefined,
          };
        }),
        best_index: asNumberOrNull(obj.best_index),
        notes: asStringArray(obj.notes),
      };
    case "validation_completed": {
      const r = asObjectOrNull(obj.result) ?? {};
      return {
        ...base,
        type,
        result: {
          approved: asBoolean(r.approved, false),
          checks: asArray(r.checks).map((c) => {
            const cc = asRecord(c) ?? {};
            return {
              name: asString(cc.name, ""),
              passed: asBoolean(cc.passed, false),
              detail: asString(cc.detail, ""),
              evidence: (asObjectOrNull(cc.evidence) ?? {}) as Record<string, unknown>,
            };
          }),
          rejection_reason: asOptionalString(r.rejection_reason),
          risk_level:
            r.risk_level === "medium" || r.risk_level === "high" ? r.risk_level : r.risk_level === "low" ? "low" : undefined,
          requires_approval: asBoolean(r.requires_approval, false),
          validated_at: asOptionalString(r.validated_at) ?? undefined,
        },
      };
    }
    case "approval_requested":
      return {
        ...base,
        type,
        action_id: asOptionalString(obj.action_id) ?? undefined,
        risk_level:
          obj.risk_level === "low" || obj.risk_level === "medium" || obj.risk_level === "high"
            ? obj.risk_level
            : undefined,
        reason: asOptionalString(obj.reason) ?? undefined,
      };
    case "approval_decided":
      return {
        ...base,
        type,
        approved: asBoolean(obj.approved, false),
        decided_by: asOptionalString(obj.decided_by) ?? undefined,
        note: asOptionalString(obj.note) ?? undefined,
      };
    case "execution_started":
      return {
        ...base,
        type,
        action_id: asOptionalString(obj.action_id) ?? undefined,
        tool: asOptionalString(obj.tool) ?? undefined,
      };
    case "execution_completed": {
      const r = asObjectOrNull(obj.result) ?? {};
      const statusRaw = r.status;
      return {
        ...base,
        type,
        result: {
          action_id: asString(r.action_id, ""),
          action_type: asOptionalString(r.action_type) ?? undefined,
          status:
            statusRaw === "failed" ||
            statusRaw === "skipped_awaiting_approval" ||
            statusRaw === "skipped_lab_offline" ||
            statusRaw === "executed"
              ? statusRaw
              : "failed",
          controller_response: (asObjectOrNull(r.controller_response) ?? {}) as Record<string, unknown>,
          flow_rules_installed: typeof r.flow_rules_installed === "number" ? r.flow_rules_installed : 0,
          error: asOptionalString(r.error),
          executed_at: asOptionalString(r.executed_at) ?? undefined,
        },
      };
    }
    case "verification_completed": {
      const o = asObjectOrNull(obj.outcome) ?? {};
      const statusRaw = o.status;
      return {
        ...base,
        type,
        outcome: {
          status:
            statusRaw === "success" ||
            statusRaw === "partial" ||
            statusRaw === "failed" ||
            statusRaw === "unverified"
              ? statusRaw
              : "unverified",
          intent_satisfied: asBoolean(o.intent_satisfied, false),
          protected_ok: asBoolean(o.protected_ok, true),
          before: (asObjectOrNull(o.before) as FlowTelemetry | null) ?? null,
          after: (asObjectOrNull(o.after) as FlowTelemetry | null) ?? null,
          protected_flows: asArray(o.protected_flows).map((p) => {
            const pf = asRecord(p) ?? {};
            return {
              flow_id: asString(pf.flow_id, ""),
              label: asString(pf.label, ""),
              latency_ms: asNumberOrNull(pf.latency_ms),
              packet_loss_pct: asNumberOrNull(pf.packet_loss_pct),
              max_latency_ms: asNumberOrNull(pf.max_latency_ms),
              max_packet_loss_pct: asNumberOrNull(pf.max_packet_loss_pct),
              ok: asBoolean(pf.ok, true),
              reason: asOptionalString(pf.reason),
              path_unmodified: asBoolean(pf.path_unmodified, true),
            };
          }),
          reason: asString(o.reason, ""),
          measured_at: asOptionalString(o.measured_at) ?? undefined,
        },
      };
    }
    case "note":
      return {
        ...base,
        type,
        level: obj.level === "warn" || obj.level === "error" ? obj.level : "info",
        message: asString(obj.message, ""),
      };
    case "message_delta":
      return { ...base, type, delta: asString(obj.delta, "") };
    case "run_completed": {
      const outcome = obj.outcome;
      const known: readonly string[] = [
        "intent_satisfied",
        "partial_improvement",
        "recovery_failed",
        "unverified",
        "abstained_insufficient_evidence",
        "action_rejected",
        "cancelled",
        "lab_offline",
        "llm_unavailable",
        "error",
      ];
      return {
        ...base,
        type,
        outcome: typeof outcome === "string" && known.includes(outcome) ? (outcome as FinalOutcome) : "unverified",
        message: asOptionalString(obj.message) ?? undefined,
      };
    }
    case "run_failed":
      return {
        ...base,
        type,
        error: asString(obj.error, "run failed"),
        outcome: asOptionalString(obj.outcome) as FinalOutcome | null,
      };
    case "run_cancelled":
      return { ...base, type, reason: asOptionalString(obj.reason) ?? undefined };
    default:
      return null;
  }
}

/* -------------------------------------------------------------------------- */
/* Reducer                                                                    */
/* -------------------------------------------------------------------------- */

const dedupeKey = (runId: string, seq: number): string => `${runId}#${seq}`;

function sortInsert(events: readonly AgentRunEvent[], event: AgentRunEvent): readonly AgentRunEvent[] {
  const out = events.slice();
  let index = out.length;
  for (let i = 0; i < out.length; i += 1) {
    if (out[i].seq > event.seq) {
      index = i;
      break;
    }
  }
  out.splice(index, 0, event);
  return out;
}

function applyEvent(state: AgentRunState, event: AgentRunEvent): AgentRunState {
  const terminal = state.status === "completed" || state.status === "cancelled" || state.status === "failed";
  let next: AgentRunState = {
    ...state,
    events: sortInsert(state.events, event),
    seen: { ...state.seen, [dedupeKey(event.run_id, event.seq)]: true },
    seq: Math.max(state.seq, event.seq),
  };

  const patch = (partial: Partial<AgentRunState>): void => {
    next = { ...next, ...partial };
  };

  if (next.runId === null && event.run_id) patch({ runId: event.run_id });
  if (next.startedAt === null) patch({ startedAt: event.ts });

  switch (event.type) {
    case "run_started":
      patch({
        status: "running",
        conversationId: event.conversation_id ?? next.conversationId,
        trigger: event.trigger ?? next.trigger,
        userMessage: event.user_message ?? next.userMessage,
      });
      break;
    case "intent_detected":
      patch({
        intent: event.intent ?? next.intent,
        needsClarification: event.needs_clarification === true,
        clarificationQuestion: event.clarification_question ?? null,
      });
      break;
    case "clarification_requested":
      patch({ needsClarification: true, clarificationQuestion: event.question });
      break;
    case "evidence_assessed":
      patch({ evidence: event.assessment });
      break;
    case "diagnosis_completed":
      patch({ diagnosisSummary: event.summary, suspectLinks: event.suspect_links ?? [] });
      break;
    case "plan_proposed":
      patch({ plan: event.action ?? next.plan });
      break;
    case "path_evaluated":
      patch({ candidatePaths: event.candidates });
      break;
    case "validation_completed":
      patch({
        validation: event.result,
        awaitingApproval: event.result.requires_approval === true && event.result.approved === false,
      });
      break;
    case "approval_requested":
      patch({ status: "waiting_approval", awaitingApproval: true });
      break;
    case "approval_decided":
      patch({
        awaitingApproval: false,
        status: event.approved ? "running" : "failed",
        approvedBy: event.decided_by ?? null,
      });
      break;
    case "execution_completed":
      patch({ execution: event.result });
      break;
    case "verification_completed":
      patch({ verification: event.outcome });
      break;
    case "message_delta":
      patch({ assistantText: next.assistantText + event.delta });
      break;
    case "note":
      if (event.level === "error") patch({ error: next.error ?? event.message });
      break;
    case "tool_failed":
      patch({ error: next.error ?? event.error });
      break;
    case "run_completed":
      patch({
        status: event.outcome === "cancelled" ? "cancelled" : "completed",
        outcome: event.outcome,
        finalMessage: event.message ?? next.finalMessage,
        finishedAt: event.ts,
        awaitingApproval: false,
      });
      break;
    case "run_failed":
      patch({
        status: "failed",
        outcome: event.outcome ?? next.outcome,
        error: event.error,
        finishedAt: event.ts,
        awaitingApproval: false,
      });
      break;
    case "run_cancelled":
      patch({
        status: "cancelled",
        cancelled: true,
        finishedAt: event.ts,
        awaitingApproval: false,
        error: next.error ?? event.reason ?? null,
      });
      break;
    default:
      break;
  }

  // Derive the folded views from the event log itself, so they can never
  // disagree with what the operator is shown.
  const toolCalls = toolCallsFromEvents(next.events);

  // A terminal run stays terminal: late frames must not resurrect it, but they
  // are still kept in `events` so nothing the server sent is hidden.
  if (terminal && event.type !== "run_completed" && event.type !== "run_failed" && event.type !== "run_cancelled") {
    patch({ status: state.status, outcome: state.outcome, finishedAt: state.finishedAt });
  }

  return { ...next, toolCalls, status: next.status === "idle" ? "running" : next.status };
}

/**
 * Apply one event. Returns the *same object reference* when the event is an
 * exact duplicate `(run_id, seq)` -- which is what an SSE reconnect replays --
 * so `setState` does not re-render on a frame it has already applied.
 *
 * A frame that reuses a `(run_id, seq)` pair but carries *different* content is
 * not a duplicate: it is kept, ordered after everything already known, because
 * silently dropping a real observation is worse than showing it.
 */
export function agentRunReducer(
  state: AgentRunState,
  event: AgentRunEvent | null | undefined,
): AgentRunState {
  if (!event) return state;
  const key = dedupeKey(event.run_id, event.seq);
  if (state.seen[key]) {
    const existing = state.events.find((e) => e.run_id === event.run_id && e.seq === event.seq);
    if (existing === undefined) return state;
    if (JSON.stringify(existing) === JSON.stringify(event)) return state;
    return applyEvent(state, { ...event, seq: state.seq + 1 });
  }
  return applyEvent(state, event);
}

/**
 * Fold a whole event list (a reconnect/resume payload, a persisted history
 * load, or a test fixture) into state. Dedupe and ordering happen per event,
 * so this is just `Array.prototype.reduce` over the reducer.
 */
export function hydrateAgentRun(
  state: AgentRunState,
  events: readonly (AgentRunEvent | null | undefined)[] | null | undefined,
): AgentRunState {
  let next = state;
  for (const event of events ?? []) next = agentRunReducer(next, event);
  return next;
}

/** Build a state from a list of raw frames (what `GET /api/runs/{id}` gives). */
export function stateFromRawEvents(raw: readonly unknown[] | null | undefined): AgentRunState {
  let state = initialAgentRunState;
  let fallback = 0;
  for (const frame of raw ?? []) {
    const event = normaliseAgentEvent(frame, fallback);
    fallback += 1;
    if (!event) {
      state = { ...state, droppedCount: state.droppedCount + 1 };
      continue;
    }
    state = agentRunReducer(state, event);
  }
  return state;
}

/** The eight spec-named groups plus extras, derived from the current events. */
export function agentRunGroups(events: readonly AgentRunEvent[] | null | undefined): ActivityGroup[] {
  return groupEvents(events);
}

/** Has the run reached a terminal event? */
export function isAgentRunFinished(state: AgentRunState): boolean {
  return state.status === "completed" || state.status === "cancelled" || state.status === "failed";
}

/** Is the run in a state where the operator can act (approve / cancel)? */
export function canCancelRun(state: AgentRunState): boolean {
  return state.status === "running" || state.status === "waiting_approval";
}

/** Human label for a run status, used by badges. */
export const RUN_STATUS_LABELS: Readonly<Record<RunStatus, string>> = {
  idle: "Idle",
  running: "Running",
  waiting_approval: "Awaiting approval",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

/** Tone for a run status, for `StatusBadge`. */
export type ToneName = "neutral" | "info" | "ok" | "warn" | "danger" | "pending" | "running";

export const RUN_STATUS_TONES: Readonly<Record<RunStatus, ToneName>> = {
  idle: "neutral",
  running: "running",
  waiting_approval: "warn",
  completed: "ok",
  failed: "danger",
  cancelled: "neutral",
};

/** Verification status -> tone. PARTIAL is never green, UNVERIFIED is never green. */
export const VERIFICATION_TONES: Readonly<Record<string, ToneName>> = {
  success: "ok",
  partial: "warn",
  failed: "danger",
  unverified: "neutral",
};

export const EVIDENCE_TONES: Readonly<Record<string, ToneName>> = {
  sufficient: "ok",
  insufficient: "warn",
  stale: "warn",
  unavailable: "danger",
};

export const EXECUTION_TONES: Readonly<Record<string, ToneName>> = {
  executed: "ok",
  failed: "danger",
  skipped_awaiting_approval: "warn",
  skipped_lab_offline: "danger",
};

export const ACTIVITY_STATUS_TONES: Readonly<Record<ActivityGroupStatus, ToneName>> = {
  pending: "pending",
  running: "running",
  complete: "ok",
  partial: "warn",
  failed: "danger",
};

export const FINAL_OUTCOME_LABELS: Readonly<Record<FinalOutcome, string>> = {
  intent_satisfied: "Intent satisfied",
  partial_improvement: "Partial improvement",
  recovery_failed: "Recovery failed",
  unverified: "Unverified",
  abstained_insufficient_evidence: "Abstained - insufficient evidence",
  action_rejected: "Action rejected",
  cancelled: "Cancelled",
  lab_offline: "Lab offline",
  llm_unavailable: "LLM unavailable",
  error: "Error",
};

export const FINAL_OUTCOME_TONES: Readonly<Record<FinalOutcome, ToneName>> = {
  intent_satisfied: "ok",
  partial_improvement: "warn",
  recovery_failed: "danger",
  unverified: "neutral",
  abstained_insufficient_evidence: "warn",
  action_rejected: "danger",
  cancelled: "neutral",
  lab_offline: "danger",
  llm_unavailable: "danger",
  error: "danger",
};

/** Relative time in a compact form, e.g. `4s`, `2m`, `1h`. */
export function formatAge(isoTimestamp: string | null | undefined, now: number = Date.now()): string {
  if (!isoTimestamp) return "";
  const then = timeToNumber(isoTimestamp);
  if (then === null) return "";
  const seconds = Math.max(0, Math.round((now - then) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.round(hours / 24)}d`;
}

/** A stable-ish short clock time. Locale dependent but deterministic per input. */
export function formatClock(isoTimestamp: string | null | undefined): string {
  if (!isoTimestamp) return "";
  const parsed = timeToNumber(isoTimestamp);
  if (parsed === null) return "";
  return new Date(parsed).toISOString().slice(11, 19);
}

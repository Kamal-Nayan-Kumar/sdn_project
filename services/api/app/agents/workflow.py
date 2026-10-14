"""The LangGraph workflow: explicit nodes, conditional routing, bounded retries.

The graph is deliberately linear-with-branches rather than a free-form agent.
Control flow that affects the network (observe -> validate -> execute ->
verify) is fixed in code; the model is consulted only for

  * interpreting a user's free-text intent,
  * summarising the evidence it was shown,
  * explaining what happened,

and every one of those outputs goes through a Pydantic schema and, where it
concerns a mutation, is *proposed* to the safety validator rather than
trusted. If the LLM is unavailable the graph falls back to deterministic
parsing so read-only inspection and pre-planned deterministic recovery still
work -- but it never claims LLM reasoning happened.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from app.agents import state as st
from app.core.config import get_settings
from app.safety.gates import (
    SafetyPolicy,
    assess_evidence,
    check_protected_flows,
    validate_reroute,
    verify_recovery,
)
from app.schemas.domain import (
    ActionType,
    ExecutionResult,
    FlowKind,
    FlowTelemetry,
    IntentSpec,
    NetworkSnapshot,
    RiskLevel,
    RerouteAction,
    VerificationStatus,
)
from app.services.lab_bridge import LabBridgeClient, LabUnavailable
from app.services.llm import LLMUnavailable, chat_json
from app.tools import run_tool

LOG = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _new_run_id() -> str:
    return "run_" + uuid.uuid4().hex[:16]


def _snapshot_from(state: st.AgentState) -> NetworkSnapshot | None:
    raw = state.get("snapshot")
    return NetworkSnapshot.model_validate(raw) if isinstance(raw, dict) else raw


def _flow(snapshot: NetworkSnapshot | None, src: str,
          dst: str) -> FlowTelemetry | None:
    if snapshot is None:
        return None
    for f in snapshot.flows:
        if f.src_host == src and f.dst_host == dst:
            return f
    return None


def _policy_for(intent: IntentSpec | None,
                snapshot: NetworkSnapshot | None) -> SafetyPolicy:
    """Build the explicit safety policy from the intent + topology.

    Protected-flow links are derived from where the protected flow currently
    runs, so a reroute cannot silently move it.
    """
    protected_links: list[str] = []
    if intent and snapshot is not None:
        for spec in intent.protected_flows:
            flow = _flow(snapshot, spec.src_host, spec.dst_host)
            if flow is None:
                continue
            path = getattr(flow, "path", None) or []
            if not path:
                continue
            # Any link the protected flow currently traverses is protected.
            protected_links.extend(f"{a}-{b}" for a, b in zip(path, path[1:]))
    return SafetyPolicy(
        protected_flows=list(intent.protected_flows) if intent else [],
        protected_links=sorted(set(protected_links)),
    )


# ---------------------------------------------------------------------------
# node 1 - intent detection
# ---------------------------------------------------------------------------


def intent_detection(state: st.AgentState) -> st.AgentState:
    """Turn a free-text request into a structured intent, or ask for one.

    Deterministic fallback: if the LLM is unavailable, a simple keyword scan
    still produces a valid intent for the canonical demo ask, and the LLM's
    non-involvement is recorded in `llm_error`.
    """
    settings = get_settings()
    message = state.get("user_message", "")

    intent = _default_intent()
    notes: list[str] = []
    llm_error = None

    if settings.llm_configured:
        try:
            parsed = chat_json(
                system=(
                    "You extract structured network intents. Respond with a "
                    "single JSON object. Never invent network facts; if the "
                    "message does not state a traffic class, source, "
                    "destination, or latency target, set the corresponding "
                    "field to null and 'needs_clarification' to true."),
                user=f"User request: {message}",
                schema_hint={
                    "traffic_class": "video|voice|web",
                    "src_host": "h1..h4",
                    "dst_host": "h1..h4",
                    "max_latency_ms": 50,
                    "needs_clarification": False,
                },
            )
            intent = IntentSpec(
                traffic_class=FlowKind(parsed.get("traffic_class") or "video"),
                src_host=parsed.get("src_host") or intent.src_host,
                dst_host=parsed.get("dst_host") or intent.dst_host,
                max_latency_ms=float(parsed.get("max_latency_ms")
                                     or intent.max_latency_ms),
            )
            notes.append("intent parsed by " + settings.groq_model)
        except LLMUnavailable as exc:
            llm_error = str(exc)
            intent = parse_intent_deterministically(message, intent)
            notes.append(f"LLM unavailable ({exc}); used deterministic intent "
                         "parsing")
        except Exception as exc:  # malformed model output
            llm_error = f"malformed LLM output: {exc}"
            intent = parse_intent_deterministically(message, intent)
            notes.append("LLM output failed schema validation; used "
                         "deterministic intent parsing")
    else:
        intent = parse_intent_deterministically(message, intent)
        notes.append("GROQ_API_KEY not configured; used deterministic intent "
                     "parsing")

    needs_clarification = not (intent.src_host and intent.dst_host
                              and intent.max_latency_ms)
    question = None
    if needs_clarification:
        question = (
            "Which traffic should I optimise? I need a source host, a "
            "destination host, and a target latency in milliseconds.")

    return {
        **state,
        "intent": intent,
        "planning_notes": notes,
        "intent_needs_clarification": needs_clarification,
        "clarification_question": question,
        "llm_error": llm_error,
    }


def _default_intent() -> IntentSpec:
    """The canonical project intent, used as a documented starting point."""
    return IntentSpec(
        name="video latency intent",
        traffic_class=FlowKind.VIDEO,
        src_host="h1",
        dst_host="h4",
        max_latency_ms=50.0,
        protected_flows=[],
    )


# Deterministic parsing of the free-text ask. Used only when the LLM is
# unavailable or unusable, so the system still honours an explicitly stated
# target instead of silently substituting the default 50 ms.
_HOST_RE = re.compile(r"\b(h[1-9]\d*)\b", re.IGNORECASE)
_LATENCY_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:ms|msec|milliseconds?)\b", re.IGNORECASE)
_KIND_RE = re.compile(r"\b(video|voice|web)\b", re.IGNORECASE)


def parse_intent_deterministically(message: str,
                                   base: IntentSpec | None = None) -> IntentSpec:
    """Extract an intent from free text without a model.

    Only *stated* facts are used: an endpoint pair, a latency figure, a traffic
    class. Anything absent keeps the default rather than being invented, which
    is the same rule the LLM prompt enforces.
    """
    intent = base.model_copy(deep=True) if base else _default_intent()
    updates: dict[str, Any] = {}

    hosts = _HOST_RE.findall(message or "")
    if len(hosts) >= 2:
        updates["src_host"] = hosts[0].lower()
        updates["dst_host"] = hosts[1].lower()
    elif len(hosts) == 1:
        # One endpoint named: adopt it, leaving the other at its default.
        named = hosts[0].lower()
        if intent.src_host.lower() != named:
            updates["dst_host"] = named
        else:
            updates["src_host"] = named

    latency = _LATENCY_RE.search(message or "")
    if latency:
        try:
            value = float(latency.group(1))
        except ValueError:
            value = 0.0
        if value > 0:
            updates["max_latency_ms"] = value

    kind = _KIND_RE.search(message or "")
    if kind:
        updates["traffic_class"] = FlowKind(kind.group(1).lower())

    # IntentSpec is frozen, so compose a new one rather than mutating.
    return intent.model_copy(update=updates) if updates else intent


# ---------------------------------------------------------------------------
# node 2 - network observation
# ---------------------------------------------------------------------------


def observe_network(state: st.AgentState,
                    client: LabBridgeClient) -> st.AgentState:
    """Collect one coherent snapshot: pings + per-link utilisation."""
    intent = state.get("intent") or _default_intent()

    # Protected flows are measured too so Gate 3 has a real "after".
    protected: dict[str, str] = {}
    for spec in intent.protected_flows:
        if spec.src_host and spec.dst_host:
            protected[spec.flow_id] = f"{spec.src_host},{spec.dst_host}"

    result = run_tool("get_network_snapshot", {
        "latency_count": 10,
        "measure_protected": protected,
    }, client)

    if not result.ok:
        return {
            **state,
            "lab_offline": True,
            "snapshot": None,
            "llm_error": result.error,
            "final_outcome": "lab_offline",
            "explanation": (
                f"The local network lab could not be read: {result.error}. "
                "No network change was attempted."),
        }

    raw = result.outputs.get("snapshot")
    snapshot = NetworkSnapshot.model_validate_json(raw) if isinstance(raw, str) else None
    if snapshot is None:
        return {**state, "lab_offline": True,
                "final_outcome": "lab_offline",
                "explanation": "The lab returned an unreadable snapshot."}

    baseline = _flow(snapshot, intent.src_host, intent.dst_host)
    protected_before = [
        f for f in snapshot.flows
        if any(f.src_host == s.src_host and f.dst_host == s.dst_host
               for s in intent.protected_flows)
    ]

    obs = {
        "captured_at": snapshot.captured_at.isoformat(),
        "video_latency_ms": baseline.latency_ms if baseline else None,
        "video_loss_pct": baseline.packet_loss_pct if baseline else None,
        "congested_links": snapshot.congested_links,
        "utilisation": {l.link_id: l.utilization_pct for l in snapshot.links},
    }
    return {
        **state,
        "snapshot": snapshot.model_dump(),
        "baseline": baseline,
        "protected_baseline": protected_before,
        "observations": state.get("observations", []) + [obs],
        "last_snapshot_at": snapshot.captured_at,
        "lab_offline": False,
    }


# ---------------------------------------------------------------------------
# node 3 - evidence sufficiency (Gate 1)
# ---------------------------------------------------------------------------


def assess_evidence_node(state: st.AgentState) -> st.AgentState:
    """Gate 1: is the evidence present, fresh, and sufficient?

    Also records `intent_violated`, because "the intent is already met" and
    "the evidence is inconclusive" must never be reported the same way: the
    first is a confident no-op, the second is a refusal to act. Collapsing them
    would make the agent look uncertain about a healthy network, or -- worse --
    make a healthy network look like a case where the agent chose not to help.
    """
    intent = state.get("intent") or _default_intent()
    snapshot = _snapshot_from(state)

    assessment = assess_evidence(
        snapshot=snapshot,
        intent=intent,
        suspected_links=state.get("suspect_links"),
    )

    notes = list(state.get("planning_notes", []))
    if not assessment.sufficient:
        notes.append(assessment.summary)

    return {
        **state,
        "evidence": assessment,
        "evidence_status": assessment.status,
        "intent_violated": _is_violated(state.get("baseline"), intent),
        "planning_notes": notes,
    }


def _is_violated(flow: FlowTelemetry | None,
                 intent: IntentSpec) -> bool | None:
    """Does the measurement breach the intent?

    Returns None when there is no usable measurement, so "unknown" never
    silently becomes "not violated".
    """
    if flow is None or flow.latency_ms is None or flow.latency_ms < 0:
        return None
    if flow.packet_loss_pct and flow.packet_loss_pct > 0:
        return True
    return flow.latency_ms > intent.max_latency_ms


# ---------------------------------------------------------------------------
# node 4 - diagnosis and planning
# ---------------------------------------------------------------------------


def diagnose_and_plan(state: st.AgentState,
                      client: LabBridgeClient) -> st.AgentState:
    """Name the likely bottleneck and enumerate candidate reroutes."""
    intent = state.get("intent") or _default_intent()
    snapshot = _snapshot_from(state)

    suspects = list(state.get("suspect_links") or [])
    if not suspects and snapshot is not None:
        from app.safety.gates import _infer_bottleneck_links

        suspects = _infer_bottleneck_links(
            snapshot, get_settings().congestion_utilization_threshold)

    # Candidate paths straight from the lab's own graph enumeration.
    paths_result = run_tool("evaluate_paths", {
        "src_host": intent.src_host,
        "dst_host": intent.dst_host,
        "avoid_links": suspects,
    }, client)

    notes = list(state.get("planning_notes", []))
    candidates: list[RerouteAction] = []

    if paths_result.ok:
        raw_candidates = paths_result.outputs.get("candidates", [])
        for cand in raw_candidates:
            nodes = cand.get("nodes") or []
            if len(nodes) < 3:
                continue
            candidates.append(RerouteAction(
                flow_id=_flow_id_for(intent),
                src_host=intent.src_host,
                dst_host=intent.dst_host,
                path=nodes,
                reason="candidate path from live topology",
            ))
        notes.append(
            f"Evaluated {len(raw_candidates)} candidate paths; "
            f"{len(candidates)} are structurally usable.")
    else:
        notes.append(f"path evaluation failed: {paths_result.error}")

    summary = (
        f"Video flow {intent.src_host}->{intent.dst_host} is measured at "
        f"{_lat(state, intent)} against a {intent.max_latency_ms:.0f} ms "
        f"intent. Suspected bottleneck link(s): "
        f"{', '.join(suspects) if suspects else 'none identified'}.")

    # The LLM may explain the evidence it can see, but not invent any.
    settings = get_settings()
    if settings.llm_configured and snapshot is not None:
        try:
            explanation = chat_json(
                system=("You summarise network telemetry for an operator. "
                        "Use ONLY the numbers given. Never guess a value."),
                user=(
                    f"Intent: video {intent.src_host}->{intent.dst_host} under "
                    f"{intent.max_latency_ms} ms.\n"
                    f"Measured latency: {_lat(state, intent)}\n"
                    f"Link utilisation: "
                    f"{ {l.link_id: l.utilization_pct for l in snapshot.links} }\n"
                    f"Suspected bottleneck(s): {suspects}\n"
                    "Summarise the likely cause in 2 sentences as a JSON object "
                    "with a 'summary' string."),
                schema_hint={"summary": "string"})
            summary = explanation.get("summary", summary)
        except Exception as exc:
            LOG.warning("LLM diagnosis skipped: %s", exc)
            notes.append(f"LLM diagnosis skipped ({exc}); using the "
                         "deterministic summary.")

    return {
        **state,
        "suspect_links": suspects,
        "candidate_actions": candidates,
        "action_id": "act_" + uuid.uuid4().hex[:16],
        "diagnosis_summary": summary,
        "planning_notes": notes,
    }


def _flow_id_for(intent: IntentSpec) -> str:
    return f"{intent.src_host}-{intent.dst_host}"


def _lat(state: st.AgentState, intent: IntentSpec) -> str:
    snap = _snapshot_from(state)
    f = _flow(snap, intent.src_host, intent.dst_host)
    if f is None or f.latency_ms is None:
        return "not measured"
    return f"{f.latency_ms:.1f} ms"


# ---------------------------------------------------------------------------
# node 5/6 - structured action selection + deterministic validation (Gate 2)
# ---------------------------------------------------------------------------


def select_and_validate(state: st.AgentState) -> st.AgentState:
    """Pick the best candidate and put it through the safety validator."""
    intent = state.get("intent") or _default_intent()
    snapshot = _snapshot_from(state)
    candidates = state.get("candidate_actions") or []
    policy = _policy_for(intent, snapshot)

    if not candidates:
        return {
            **state,
            "selected_action": None,
            "validation": None,
            "final_outcome": "action_rejected",
            "explanation": "No usable alternative path exists in the current "
                           "topology, so no reroute could be proposed.",
        }

    rejected: list[dict[str, Any]] = list(state.get("rejected_actions", []))
    settings = get_settings()
    run_id = state.get("run_id", "")

    for index, action in enumerate(candidates):
        result = validate_reroute(
            action,
            snapshot=snapshot,
            policy=policy,
            run_id=run_id,
            action_id=state.get("action_id", ""),
        )
        if result.approved:
            notes = list(state.get("planning_notes", []))
            notes.append(
                f"Safety validation approved candidate #{index + 1} "
                f"({result.checks_passed} checks passed).")
            return {
                **state,
                "selected_action": action,
                "validation": result,
                "rejected_actions": rejected,
                "planning_notes": notes,
            }
        rejected.append({
            "action": action.model_dump(),
            "rejection_reason": result.rejection_reason,
            "failed_checks": [c.name for c in result.failed_checks],
        })

    # Nothing passed. This is the *correct* outcome for an unsafe topology,
    # and it is reported rather than worked around.
    reason = rejected[-1]["rejection_reason"] if rejected else "unknown"
    notes = list(state.get("planning_notes", []))
    notes.append(
        f"All {len(candidates)} candidate paths were rejected by the "
        "deterministic safety validator.")
    return {
        **state,
        "selected_action": None,
        "validation": None,
        "rejected_actions": rejected,
        "planning_notes": notes,
        "final_outcome": "action_rejected",
        "explanation": ("Every candidate reroute failed deterministic safety "
                        f"validation, so no network change was made. "
                        f"Last rejection: {reason}"),
    }


# ---------------------------------------------------------------------------
# node 7 - execution
# ---------------------------------------------------------------------------


def execute_action(state: st.AgentState,
                   client: LabBridgeClient) -> st.AgentState:
    """Execute an *approved* action through the controller."""
    settings = get_settings()
    action = state.get("selected_action")
    validation = state.get("validation")

    if action is None or validation is None or not validation.approved:
        return {**state, "final_outcome": "action_rejected",
                "explanation": "No approved action to execute."}

    # Optional human approval for medium/high-risk actions.
    if (validation.requires_approval
            and settings.require_human_approval_for_risky_actions
            and not state.get("approved_by")):
        return {**state, "awaiting_approval": True,
                "final_outcome": None,
                "explanation": "Action approved by the safety validator but "
                               f"risk level '{validation.risk_level.value}' "
                               "requires explicit human approval."}

    if state.get("cancelled"):
        return {**state, "final_outcome": "cancelled"}

    result = run_tool("apply_reroute", {
        "action_id": state.get("action_id", ""),
        "flow_id": action.flow_id,
        "src_host": action.src_host,
        "dst_host": action.dst_host,
        "path": action.path,
    }, client)

    if not result.ok:
        return {**state, "execution": None, "lab_offline": True,
                "final_outcome": "lab_offline",
                "explanation": f"Execution failed: {result.error}"}

    # Build the typed result the verification gate reads. Storing the raw tool
    # payload here would leave `status` absent, and Gate 3 would then report
    # "the action did not execute" for a reroute that demonstrably did.
    outputs = result.outputs or {}
    controller = outputs.get("controller") or {}
    execution = ExecutionResult(
        action_id=state.get("action_id", ""),
        action_type=ActionType.REROUTE_FLOW,
        status="executed",
        controller_response=outputs,
        flow_rules_installed=int(controller.get("flow_rules_installed", 0) or 0),
    )

    return {
        **state,
        "execution": execution,
        "awaiting_approval": False,
        "executed_path_ids": [action.path],
    }


# ---------------------------------------------------------------------------
# node 8 - fresh post-action observation
# ---------------------------------------------------------------------------


def observe_after(state: st.AgentState,
                  client: LabBridgeClient) -> st.AgentState:
    """Re-measure the network *after* the change."""
    intent = state.get("intent") or _default_intent()
    result = run_tool("get_network_snapshot", {
        "latency_count": 10,
        "measure_protected": {
            s.flow_id: f"{s.src_host},{s.dst_host}"
            for s in intent.protected_flows
            if s.src_host and s.dst_host
        },
    }, client)

    if not result.ok:
        return {**state, "after": None, "protected_after": [],
                "final_outcome": "unverified",
                "explanation": (
                    "Post-action telemetry could not be collected, so recovery "
                    "is unverified. A successful command is not a verified "
                    "recovery.")}

    raw = result.outputs.get("snapshot")
    try:
        snapshot = NetworkSnapshot.model_validate_json(raw)
    except Exception as exc:
        return {**state, "after": None, "final_outcome": "unverified",
                "explanation": f"Post-action snapshot unreadable: {exc}"}

    after = _flow(snapshot, intent.src_host, intent.dst_host)
    protected_after = [
        f for f in snapshot.flows
        if any(f.src_host == s.src_host and f.dst_host == s.dst_host
               for s in intent.protected_flows)
    ]
    return {**state, "after": after, "protected_after": protected_after,
            "snapshot": snapshot.model_dump()}


# ---------------------------------------------------------------------------
# node 9/10 - verification (Gate 3) + explanation + audit
# ---------------------------------------------------------------------------


def verify_and_explain(state: st.AgentState,
                       client: LabBridgeClient) -> st.AgentState:
    """Gate 3: compare fresh measurements against the intent."""
    intent = state.get("intent") or _default_intent()
    before = state.get("baseline")
    after = state.get("after")
    protected_before = list(state.get("protected_baseline", []))
    protected_after = list(state.get("protected_after", []))

    execution = state.get("execution")
    if not isinstance(execution, ExecutionResult) or execution.status != "executed":
        return {
            **state,
            "verification": None,
            "verification_status": VerificationStatus.UNVERIFIED,
            "final_outcome": "unverified",
            "explanation": ("The action did not execute, so there is nothing "
                            "to verify against the intent."),
        }

    executed_path = list(execution.controller_response.get("path") or [])
    statuses = check_protected_flows(
        protected=intent.protected_flows,
        before=protected_before,
        after=protected_after,
        path_by_flow={spec.flow_id: executed_path
                      for spec in intent.protected_flows},
    )

    outcome = verify_recovery(
        intent=intent,
        before=before,
        after=after,
        protected=intent.protected_flows,
        protected_statuses=statuses,
    )

    final = {
        VerificationStatus.SUCCESS: "intent_satisfied",
        VerificationStatus.PARTIAL: "partial_improvement",
        VerificationStatus.FAILED: "recovery_failed",
        VerificationStatus.UNVERIFIED: "unverified",
    }[outcome.status]

    notes = list(state.get("planning_notes", []))
    notes.append(outcome.reason)

    return {
        **state,
        "verification": outcome,
        "verification_status": outcome.status,
        "final_outcome": final,
        "explanation": outcome.reason,
        "finished_at": utcnow(),
        "planning_notes": notes,
    }


def _default_intent_public() -> IntentSpec:
    return _default_intent()


__all__ = [
    "assess_evidence_node",
    "diagnose_and_plan",
    "execute_action",
    "intent_detection",
    "observe_after",
    "observe_network",
    "select_and_validate",
    "verify_and_explain",
]

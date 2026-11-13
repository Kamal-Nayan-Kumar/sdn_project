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
    VIOLATION_SAMPLE_FRACTION,
    SafetyPolicy,
    assess_evidence,
    check_protected_flows,
    latency_violation,
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
    ProtectedFlowSpec,
    RiskLevel,
    RerouteAction,
    VerificationStatus,
)
from app.services.lab_bridge import LabBridgeClient, LabUnavailable
from app.services.llm import LLMUnavailable, chat_json, chat_stream
from app.streaming.emit import emit, emit_state
from app.streaming.events import EventType
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


def _tool_observer(state: st.AgentState, step: str):
    """Publish `tool_started` / `tool_completed` while a node runs."""
    run_id = state.get("run_id")

    def observe(phase: str, tool: str, data: dict[str, Any]) -> None:
        if phase == "started":
            emit(run_id, EventType.TOOL_STARTED, step=step, tool=tool,
                 inputs=data.get("inputs") or {})
        else:
            emit(run_id, EventType.TOOL_COMPLETED, step=step, tool=tool,
                 ok=bool(data.get("ok")), duration_ms=data.get("duration_ms"),
                 error=data.get("error"), summary=data.get("summary"))

    return observe


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
        intent_src=intent.src_host if intent else None,
        intent_dst=intent.dst_host if intent else None,
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
            notes.append("intent parsed by " + settings.llm_model_name)
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
        notes.append("no LLM key configured; used deterministic intent "
                     "parsing")

    needs_clarification = not _message_is_actionable(message)
    question = None
    if needs_clarification:
        question = (
            "Which traffic should I optimise? Tell me the flow (for example "
            "h1 to h4) and the latency target in milliseconds, or describe "
            "the symptom you are seeing.")

    emit_state(
        state, EventType.INTENT_DETECTED, step="intent",
        src_host=intent.src_host,
        dst_host=intent.dst_host,
        max_latency_ms=intent.max_latency_ms,
        traffic_class=intent.traffic_class.value,
        needs_clarification=needs_clarification,
        parsed_by=notes[-1] if notes else "deterministic",
    )

    return {
        **state,
        "intent": intent,
        "planning_notes": notes,
        "intent_needs_clarification": needs_clarification,
        "clarification_question": question,
        "llm_error": llm_error,
    }


def _default_intent() -> IntentSpec:
    """The canonical project intent, used as a documented starting point.

    The protected voice flow $h3 \to h4$ is declared here rather than left
    implicit: a flow that nobody declares is a flow nobody protects, and the
    protected-flow machinery in the safety gate and in the verification gate
    would never be exercised by a real run.
    """
    return IntentSpec(
        name="video latency intent",
        traffic_class=FlowKind.VIDEO,
        src_host="h1",
        dst_host="h4",
        max_latency_ms=50.0,
        protected_flows=[
            ProtectedFlowSpec(
                flow_id="h3->h4",
                label="voice",
                src_host="h3",
                dst_host="h4",
                max_latency_ms=20.0,
                max_packet_loss_pct=1.0,
            ),
        ],
    )


# Deterministic parsing of the free-text ask. Used only when the LLM is
# unavailable or unusable, so the system still honours an explicitly stated
# target instead of silently substituting the default 50 ms.
_HOST_RE = re.compile(r"\b(h[1-9]\d*)\b", re.IGNORECASE)
_LATENCY_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:ms|msec|milliseconds?)\b", re.IGNORECASE)
_KIND_RE = re.compile(r"\b(video|voice|web)\b", re.IGNORECASE)
_ACTION_RE = re.compile(
    r"\b(fix|optimis|optimiz|reroute|recover|heal|latency|slow|congest|"
    r"violat|degrad|health|check|status)\w*", re.IGNORECASE)


def _message_is_actionable(message: str) -> bool:
    """Does the request name something the agent can act on?

    A bare greeting must produce a clarifying question, not a reroute of the
    default flow. Any stated endpoint, latency target, traffic class or
    network action is enough to proceed; the default intent supplies the rest.
    """
    text = message or ""
    return bool(_HOST_RE.search(text) or _LATENCY_RE.search(text)
                or _KIND_RE.search(text) or _ACTION_RE.search(text))


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
    }, client, observer=_tool_observer(state, "observe"))

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

    emit_state(
        state, EventType.EVIDENCE_ASSESSED, step="evidence",
        status=assessment.status.value,
        sufficient=bool(assessment.sufficient),
        confidence=assessment.confidence,
        summary=assessment.summary,
        missing=list(getattr(assessment, "missing", []) or []),
        stale=list(getattr(assessment, "stale", []) or []),
    )
    if not assessment.sufficient:
        emit_state(
            state, EventType.GUARDRAIL, step="evidence",
            stage="evidence", decision="block", rule="evidence_gate",
            reason=assessment.summary,
        )

    baseline = state.get("baseline")
    latency_violated = _is_violated(baseline, intent)
    # The loss half of the intent. Eq. (4) of the paper is a latency predicate
    # on purpose, but a flow whose packet loss is above its declared loss
    # target has not met the intent either -- and if Gate 1 called that a
    # satisfied intent, the two gates would disagree about the same
    # measurement, which is exactly what the shared predicate exists to
    # prevent. With no loss target declared (the evaluated intent declares
    # none) this is false by construction, so the behaviour is unchanged.
    loss_target = getattr(intent, "max_packet_loss_pct", None)
    loss_violated = False
    if loss_target is not None and baseline is not None:
        loss = getattr(baseline, "packet_loss_pct", None)
        if loss is not None and loss > float(loss_target):
            loss_violated = True

    return {
        **state,
        "evidence": assessment,
        "evidence_status": assessment.status,
        "intent_violated": latency_violated,
        "intent_loss_violated": loss_violated,
        "planning_notes": notes,
    }


# How many of a flow's individual samples may exceed the latency target before
# the intent counts as breached. The lab measures each flow with a 10-packet
# ping, so a single slow packet from scheduler noise is not a violation -- but a
# majority, or any sustained run, is. Expressed as a fraction so it does not
# depend on the sample count the lab happens to use.
#
# The constant is defined in `safety.gates` and imported above, so that the
# evidence gate and the verification gate cannot drift apart.


def _is_violated(flow: FlowTelemetry | None,
                 intent: IntentSpec) -> bool | None:
    """Does the measurement breach the intent?

    Returns None when there is no usable measurement, so "unknown" never
    silently becomes "not violated".

    The verdict uses the individual samples when the lab reported them, not
    just their mean. Comparing the mean alone made the verdict a coin flip on
    a flow oscillating around the threshold: a run that sampled 9 packets at
    40-56 ms against a 50 ms target saw a mean of 41.8 ms, concluded
    `intent_satisfied`, and proposed nothing -- while the dashboard showed the
    same flow breaching. The mean hides exactly the behaviour a latency SLO is
    meant to catch.

    The rule itself lives in `safety.gates.latency_violation`, so that the
    evidence gate and the verification gate cannot drift apart.
    """
    if flow is None or flow.latency_ms is None or flow.latency_ms < 0:
        return None
    if flow.packet_loss_pct and flow.packet_loss_pct > 0:
        return True
    return latency_violation(flow, intent.max_latency_ms)


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

        # The flow's current path is needed for the path-delay fallback: when
        # nothing is congested on byte counters, the only place the excess
        # latency can be accruing is a link on the path the flow is already
        # taking. Without it the diagnosis reports "no bottleneck identified"
        # even though the agent is about to reroute around one.
        current_path = list((state.get("baseline") and state["baseline"].path) or [])
        suspects = _infer_bottleneck_links(
            snapshot, get_settings().congestion_utilization_threshold,
            path=current_path or None)

    # Candidate paths straight from the lab's own graph enumeration.
    paths_result = run_tool("evaluate_paths", {
        "src_host": intent.src_host,
        "dst_host": intent.dst_host,
        "avoid_links": suspects,
    }, client, observer=_tool_observer(state, "diagnose"))

    notes = list(state.get("planning_notes", []))
    candidates: list[RerouteAction] = []

    if paths_result.ok:
        raw_candidates = paths_result.outputs.get("candidates", [])
        current = list((state.get("baseline") and
                       state["baseline"].path) or [])
        for cand in raw_candidates:
            nodes = cand.get("nodes") or []
            if len(nodes) < 3:
                continue
            # A candidate identical to the path the flow is already taking is a
            # no-op. Installing it changes nothing, then the post-action
            # measurement comes back the same and the run reports a failure
            # that the action could not possibly have prevented.
            if current and _same_path(nodes, current):
                notes.append("skipped a candidate equal to the current path")
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
            f"{len(candidates)} are usable and differ from the current path.")
    else:
        notes.append(f"path evaluation failed: {paths_result.error}")

    summary = (
        f"Video flow {intent.src_host}->{intent.dst_host} is measured at "
        f"{_lat(state, intent)} against a {intent.max_latency_ms:.0f} ms "
        f"intent. Suspected bottleneck link(s): "
        f"{', '.join(suspects) if suspects else 'none identified'}.")

    # The LLM may explain the evidence it can see, but not invent any. Its
    # prose streams to the operator while it is generated, so the diagnosis
    # step shows live reasoning instead of appearing fully formed.
    settings = get_settings()
    model_used = False
    if settings.llm_configured and snapshot is not None:
        utilisation = {l.link_id: l.utilization_pct for l in snapshot.links}
        try:
            streamed = chat_stream(
                system=(
                    "You are the diagnosis step of a network recovery agent. "
                    "Explain the likely cause in at most three short sentences. "
                    "Use ONLY the numbers and links given. Never invent a "
                    "measurement, a host, or a link. Do not suggest disabling a "
                    "safety check. Plain prose, no markdown, no preamble."),
                user=(
                    f"Intent: {intent.traffic_class.value} "
                    f"{intent.src_host}->{intent.dst_host} under "
                    f"{intent.max_latency_ms:.0f} ms.\n"
                    f"Measured latency: {_lat(state, intent)}\n"
                    f"Link utilisation (%): {utilisation}\n"
                    f"Congested links: {snapshot.congested_links}\n"
                    f"Suspected bottleneck(s): {suspects or 'none identified'}\n"
                    f"Candidate alternative paths: "
                    f"{['-'.join(c.path) for c in candidates] or 'none'}"),
                on_delta=lambda text: emit_state(
                    state, EventType.THINKING, step="diagnose", text=text),
            )
            if streamed:
                summary = streamed
                model_used = True
        except LLMUnavailable as exc:
            LOG.warning("LLM diagnosis skipped: %s", exc)
            notes.append(f"LLM diagnosis skipped ({exc}); using the "
                         "deterministic summary.")
        except Exception as exc:
            LOG.warning("LLM diagnosis failed: %s", exc)
            notes.append(f"LLM diagnosis failed ({exc}); using the "
                         "deterministic summary.")

    if not model_used:
        emit_state(state, EventType.THINKING, step="diagnose", text=summary)

    emit_state(state, EventType.DIAGNOSIS_SUMMARY, step="diagnose",
               summary=summary, model=settings.llm_model_name if model_used else None,
               suspect_links=suspects,
               candidates=["-".join(c.path) for c in candidates])

    return {
        **state,
        "suspect_links": suspects,
        "candidate_actions": candidates,
        "action_id": "act_" + uuid.uuid4().hex[:16],
        "diagnosis_summary": summary,
        "planning_notes": notes,
    }


def _path_delay(snapshot: NetworkSnapshot | None, path: list[str]) -> float:
    """The accumulated effective delay of a switch path.

    Only the inter-switch edges count: the host access edges are a fixed
    $0.1$~ms each and would not change the comparison between two paths.
    """
    if snapshot is None or not path:
        return 0.0
    delays = {link.link_id: float(link.latency_ms or 0.0)
              for link in snapshot.links}
    total = 0.0
    for a, b in zip(path, path[1:]):
        if a.startswith("h") or b.startswith("h"):
            # Host access edges are not switch edges: they are a fixed
            # 0.1 ms each and would not change the comparison.
            continue
        total += float(delays.get("-".join(sorted((a, b))), 0.0))
    return total


def _switch_nodes(path: list[str]) -> list[str]:
    """The switch subsequence of a path, e.g. [h1,s1,s3,s4,h4] -> [s1,s3,s4]."""
    return [n for n in path if not n.startswith("h")]


def _checks(result: Any) -> list[dict[str, Any]]:
    """Serialise validator checks for the stream and the audit record."""
    return [
        {"name": c.name, "passed": bool(c.passed), "detail": c.detail,
         "evidence": getattr(c, "evidence", None)}
        for c in (getattr(result, "checks", None) or [])
    ]


def _same_path(a: list[str], b: list[str]) -> bool:
    """Do two node paths traverse the same network edges?

    Compared over the switch subsequence, because the lab reports a flow's
    current path as switches only while a candidate path includes its host
    endpoints. Comparing host endpoints too would make every pair look
    different, so a candidate identical to the live path would slip through.
    """
    return _switch_edges(a) == _switch_edges(b)


def _switch_edges(path: list[str]) -> list[str]:
    hops = [n for n in path if not n.startswith("h")] or list(path)
    return [f"{min(x, y)}-{max(x, y)}" for x, y in zip(hops, hops[1:])]


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

    # A reroute is only worth making if the path it installs carries less
    # delay than the path the flow is on now. Without this comparison the
    # planner would drop the active path and install an alternative that is
    # strictly worse -- degrading a healthy network to satisfy an intent the
    # topology cannot meet, and leaving the verification gate to report the
    # regression it had caused.
    baseline = state.get("baseline")
    current_path = list(getattr(baseline, "path", None) or [])
    current_delay = _path_delay(snapshot, current_path)
    no_better = 0

    for index, action in enumerate(candidates):
        candidate_delay = _path_delay(snapshot, _switch_nodes(action.path))
        if candidate_delay >= current_delay:
            no_better += 1
            rejected.append({
                "action": action.model_dump(),
                "rejection_reason": (
                    f"the candidate carries {candidate_delay:.1f} ms of link "
                    f"delay, which is not less than the {current_delay:.1f} ms "
                    f"the flow is on now"),
                "failed_checks": ["not_an_improvement"],
            })
            continue
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
            emit_state(
                state, EventType.VALIDATION_COMPLETED, step="validate",
                status="approved", approved=True, checks=_checks(result),
                checks_passed=result.checks_passed,
                risk_level=result.risk_level.value,
                requires_approval=bool(result.requires_approval),
                rejection_reason=None)
            emit_state(
                state, EventType.GUARDRAIL, step="validate",
                stage="validation", decision="allow", rule="reroute_validator",
                reason=(f"{result.checks_passed} deterministic checks passed "
                        f"(risk {result.risk_level.value})."))
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

    # Nothing passed. There are two quite different reasons for that, and
    # they are reported differently: no candidate was *safe*, or no candidate
    # was *worth doing*. The second is the system's ability to decline to
    # act, which is the point of the whole design.
    reason = rejected[-1]["rejection_reason"] if rejected else "unknown"
    notes = list(state.get("planning_notes", []))
    only_no_better = bool(rejected) and no_better == len(rejected)
    if only_no_better:
        notes.append(
            f"All {len(candidates)} candidate paths carry at least as much "
            f"link delay as the path the flow is already on "
            f"({current_delay:.1f} ms); none was adopted.")
        outcome = "no_better_path"
        explanation = (
            "No alternative path carries less link delay than the one the "
            f"flow is on now ({current_delay:.1f} ms), so the agent declined "
            "to change the network: the current path is already the best "
            "available, and rerouting would only make the intent harder to "
            "meet.")
        status = "no_better_path"
    else:
        notes.append(
            f"All {len(candidates)} candidate paths were rejected by the "
            "deterministic safety validator.")
        outcome = "action_rejected"
        explanation = ("Every candidate reroute failed deterministic safety "
                        f"validation, so no network change was made. "
                        f"Last rejection: {reason}")
        status = "rejected"
    emit_state(
        state, EventType.VALIDATION_COMPLETED, step="validate",
        status=status, approved=False, checks=[],
        checks_passed=0, risk_level=None, requires_approval=False,
        rejection_reason=reason, rejected_actions=rejected)
    emit_state(
        state, EventType.GUARDRAIL, step="validate",
        stage="validation", decision="block", rule="reroute_validator",
        reason=f"No candidate was adopted. {reason}")
    return {
        **state,
        "selected_action": None,
        "validation": None,
        "rejected_actions": rejected,
        "planning_notes": notes,
        "final_outcome": outcome,
        "explanation": explanation,
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
    }, client, observer=_tool_observer(state, "execute"))

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

    emit_state(
        state, EventType.EXECUTION_COMPLETED, step="execute",
        status="executed",
        flow_rules_installed=execution.flow_rules_installed,
        path=controller.get("path") or action.path,
        action_id=execution.action_id,
        flow_id=action.flow_id,
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
        # The action did not execute. If a previous node already decided *why*
        # (the lab rejected the mutation, the run was cancelled, an approval
        # was withheld), that reason is the outcome. Reporting "unverified"
        # here would overwrite a concrete failure with a vague one and, worse,
        # claim that post-action telemetry was missing when it was not.
        preserved = state.get("final_outcome")
        if preserved in ("lab_offline", "cancelled", "awaiting_approval",
                         "action_rejected"):
            return {
                **state,
                "verification": None,
                "verification_status": VerificationStatus.UNVERIFIED,
                "explanation": state.get("explanation")
                or "The action did not execute, so there is nothing to "
                   "verify against the intent.",
            }
        return {
            **state,
            "verification": None,
            "verification_status": VerificationStatus.UNVERIFIED,
            "final_outcome": "unverified",
            "explanation": ("The action did not execute, so there is nothing "
                            "to verify against the intent."),
        }

    statuses = check_protected_flows(
        protected=intent.protected_flows,
        before=protected_before,
        after=protected_after,
        # Each protected flow is compared against the path it was on before
        # the action. Passing the path the action installed would compare a
        # protected flow against the *action's* path and report every
        # protected flow as having been moved.
        path_by_flow={spec.flow_id: list(
            (next((f.path for f in protected_after
                   if f.src_host == spec.src_host
                   and f.dst_host == spec.dst_host), None) or []))
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

    emit_state(
        state, EventType.VERIFICATION_COMPLETED, step="verify",
        status=outcome.status.value,
        intent_satisfied=bool(outcome.intent_satisfied),
        protected_ok=bool(outcome.protected_ok),
        before_ms=getattr(before, "latency_ms", None) if before else None,
        after_ms=getattr(after, "latency_ms", None) if after else None,
        reason=outcome.reason,
    )

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

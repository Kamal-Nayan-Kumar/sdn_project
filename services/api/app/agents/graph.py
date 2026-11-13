"""LangGraph assembly for the self-healing workflow.

The topology is fixed in code rather than chosen by the model:

    intent -> observe -> evidence -> diagnose -> validate -> execute
           -> observe_after -> verify

with two conditional edges that encode the trustworthiness policy:

*   `evidence_gate`  (Gate 1) -- without sufficient, fresh evidence the run
    stops as `abstained_insufficient_evidence`. It never proceeds to plan.
*   `validation_gate` (Gate 2) -- if the deterministic validator rejects every
    candidate, execution is skipped entirely.

Nodes that talk to the lab take a `LabBridgeClient`, so the client is bound at
graph-build time rather than looked up globally. That keeps a run's bridge
scoped to one lab session and makes the graph trivially testable.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Callable

from langgraph.graph import END, StateGraph

from app.agents import state as st
from app.agents import workflow as wf
from app.services.lab_bridge import LabBridgeClient

LOG = logging.getLogger(__name__)

Node = Callable[[st.AgentState], st.AgentState]


def _evidence_route(state: st.AgentState) -> str:
    """Gate 1: three outcomes, never conflated.

    * `diagnose`   -- the intent is breached *and* the evidence supports acting.
    * `satisfied`  -- the intent was measured and already met on fresh,
      sane, sufficient evidence. A confident no-op, reported as such rather
      than as a failure to act. A stale or insane snapshot never routes
      here, even when its samples satisfy the target.
    * `abstain`    -- the measurement is missing, stale, insane or
      inconclusive. A refusal to act, which must not be reported as success.
    """
    violated = state.get("intent_violated")
    loss_violated = state.get("intent_loss_violated")
    status = state.get("evidence_status")
    sufficient = status is not None and status.value == "sufficient"

    # Freshness and numerical sanity are preconditions for a confident no-op:
    # a stale or insane snapshot whose samples satisfy the target must not be
    # reported as "the intent is met". Only fresh, sane, sufficient evidence
    # may terminate the run as satisfied.
    if violated is False and not loss_violated:
        return "satisfied" if sufficient else "abstain"

    if sufficient:
        return "diagnose"
    return "abstain"


def _validation_route(state: st.AgentState) -> str:
    """Gate 2: only an approved action may reach the data plane.

    An action that the validator approved but that sits above the unattended
    risk threshold is *held* for a human rather than executed. The decision
    has to be made here, at the routing point: the `execute` node is wired
    unconditionally to `observe_after`, so a hold taken inside `execute`
    would fall through to the verification gate and be reported as an
    unverified recovery instead of as a held action.
    """
    if state.get("awaiting_approval"):
        return "hold"
    validation = state.get("validation")
    if validation is None or not validation.approved:
        return "stop"
    if getattr(validation, "requires_approval", False) and not state.get("approved_by"):
        return "hold"
    return "execute"


def _terminal(state: st.AgentState) -> st.AgentState:
    """Closing node: stamp the finish time and leave the outcome untouched.

    One exception: a run that stopped to ask the operator a question has no
    outcome of its own yet, and the record would otherwise be filed as
    ``unknown`` -- which reads like a measurement failure rather than the
    deliberate refusal it is. The clarification branch gets its own terminal
    outcome so that "I asked" is auditable, exactly like every other exit.
    """
    outcome = state.get("final_outcome")
    if not outcome and state.get("intent_needs_clarification"):
        outcome = "clarification"
    return {**state, "final_outcome": outcome, "finished_at": wf.utcnow()}


def build_graph(client: LabBridgeClient):
    """Compile the workflow with `client` bound into every lab-facing node."""
    graph = StateGraph(st.AgentState)

    graph.add_node("intent", wf.intent_detection)
    graph.add_node("observe", _bind(wf.observe_network, client))
    graph.add_node("evidence", wf.assess_evidence_node)
    graph.add_node("diagnose", _bind(wf.diagnose_and_plan, client))
    graph.add_node("validate", wf.select_and_validate)
    graph.add_node("execute", _bind(wf.execute_action, client))
    graph.add_node("observe_after", _bind(wf.observe_after, client))
    graph.add_node("verify", _bind(wf.verify_and_explain, client))
    graph.add_node("abstain", _abstain)
    graph.add_node("satisfied", _satisfied)
    graph.add_node("stop", _terminal)
    graph.add_node("hold", _hold)
    graph.add_node("finish", _terminal)

    graph.set_entry_point("intent")

    graph.add_conditional_edges(
        "intent",
        _needs_clarification,
        {"clarify": "stop", "continue": "observe"},
    )
    graph.add_edge("observe", "evidence")
    graph.add_conditional_edges(
        "evidence",
        _evidence_route,
        {"diagnose": "diagnose", "satisfied": "satisfied", "abstain": "abstain"},
    )
    graph.add_edge("diagnose", "validate")
    graph.add_conditional_edges(
        "validate",
        _validation_route,
        {"execute": "execute", "hold": "hold", "stop": "stop"},
    )
    graph.add_edge("execute", "observe_after")
    graph.add_edge("observe_after", "verify")
    graph.add_edge("verify", "finish")
    graph.add_edge("abstain", "stop")
    graph.add_edge("satisfied", "stop")
    graph.add_edge("stop", END)
    graph.add_edge("hold", END)
    graph.add_edge("finish", END)

    return graph.compile()


def _bind(node: Callable[..., st.AgentState], client: LabBridgeClient) -> Node:
    """Adapt `f(state, client)` to the graph's `f(state)` calling convention."""

    def wrapped(state: st.AgentState) -> st.AgentState:
        return node(state, client)

    wrapped.__name__ = getattr(node, "__name__", "node")
    return wrapped


def _needs_clarification(state: st.AgentState) -> str:
    if state.get("intent_needs_clarification"):
        return "clarify"
    return "continue"


def _abstain(state: st.AgentState) -> st.AgentState:
    """Gate 1 refusal: no evidence, therefore no plan, therefore no change.

    A refusal is not always the whole story: if the observation step already
    established that the lab was unreachable, that is the real reason, and
    "insufficient evidence" would hide it behind a vaguer word.
    """
    evidence = state.get("evidence")
    reason = getattr(evidence, "reason", None) or "insufficient evidence"
    notes = list(state.get("planning_notes", []))
    notes.append(f"Abstained: {reason}")

    if state.get("lab_offline"):
        return {
            **state,
            "planning_notes": notes,
            "final_outcome": "lab_offline",
            "explanation": (
                "The local network lab could not be read, so the network "
                "state is unknown. No network change was attempted."),
        }
    return {
        **state,
        "planning_notes": notes,
        "final_outcome": "abstained_insufficient_evidence",
        "explanation": (
            "The available telemetry does not establish that the intent is "
            f"being violated ({reason}), so the agent did not propose any "
            "network change."),
    }


def _satisfied(state: st.AgentState) -> st.AgentState:
    """Nothing to heal: the measurement already meets the intent.

    Reported distinctly from `abstained_insufficient_evidence` so the audit
    trail never suggests uncertainty that the telemetry did not contain.
    """
    intent = state.get("intent") or wf._default_intent()
    target = intent.max_latency_ms
    return {
        **state,
        "final_outcome": "intent_satisfied",
        "explanation": (
            f"The measured latency of {wf._lat(state, intent)} already meets "
            f"the {target:.0f} ms intent, so no network change was proposed."),
    }


def _hold(state: st.AgentState) -> st.AgentState:
    """Gate 2 hold: an approved-but-risky action is waiting for a human.

    The network was not touched, so this is *not* a rejection and *not* a
    recovery attempt: it gets its own outcome rather than being folded into
    `action_rejected`, which means "the validator refused this action".
    """
    return {
        **state,
        "awaiting_approval": True,
        "final_outcome": "awaiting_approval",
        "explanation": state.get("explanation")
        or "Action is awaiting human approval; nothing was changed.",
    }


@lru_cache(maxsize=1)
def default_graph() -> Any:
    """The compiled graph with a fresh lab bridge.

    Cached so the graph is compiled once per process; the bridge client is
    stateless over the unix socket, so sharing it across concurrent runs is
    safe.
    """
    return build_graph(LabBridgeClient())


__all__ = ["build_graph", "default_graph"]
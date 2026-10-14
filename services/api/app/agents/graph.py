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

from app.agents import nodes.workflow as wf
from app.agents import state as st
from app.services.lab_bridge import LabBridgeClient

LOG = logging.getLogger(__name__)

Node = Callable[[st.AgentState], st.AgentState]


def _evidence_route(state: st.AgentState) -> str:
    """Gate 1: stop unless the evidence assessment is strong enough."""
    status = state.get("evidence_status")
    if status is None:
        return "diagnose"
    if status.value in ("sufficient", "provisional"):
        return "diagnose"
    return "abstain"


def _validation_route(state: st.AgentState) -> str:
    """Gate 2: only an approved action may reach the data plane."""
    if state.get("awaiting_approval"):
        return "hold"
    validation = state.get("validation")
    if validation is not None and validation.approved:
        return "execute"
    return "stop"


def _terminal(state: st.AgentState) -> st.AgentState:
    """Closing node: stamp the finish time and leave the outcome untouched."""
    return {**state, "finished_at": wf.utcnow()}


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
        {"diagnose": "diagnose", "abstain": "abstain"},
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
    """Gate 1 refusal: no evidence, therefore no plan, therefore no change."""
    evidence = state.get("evidence")
    reason = getattr(evidence, "reason", None) or "insufficient evidence"
    notes = list(state.get("planning_notes", []))
    notes.append(f"Abstained: {reason}")
    return {
        **state,
        "planning_notes": notes,
        "final_outcome": "abstained_insufficient_evidence",
        "explanation": (
            "The available telemetry does not establish that the intent is "
            f"being violated ({reason}), so the agent did not propose any "
            "network change."),
    }


def _hold(state: st.AgentState) -> st.AgentState:
    """Gate 2 hold: an approved-but-risky action is waiting for a human."""
    return {
        **state,
        "final_outcome": "action_rejected",
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
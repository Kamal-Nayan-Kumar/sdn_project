"""HTTP surface for agent runs and their event streams.

Two things this module refuses to do:

*   It never lets a client poll the graph directly. A run is started, its id is
    returned, and progress arrives only through the SSE stream. That keeps the
    audit trail single-sourced.
*   It never invents a run. An unknown run id is a 404, not an empty stream --
    a client waiting forever on a stream that will never produce a terminal
    event is worse than one told immediately.

The graph is driven with `stream_mode="updates"`, so each node's events reach
the operator as that node finishes rather than in one burst at the end. Model
diagnosis and the final answer stream token by token on the same connection.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from queue import Queue
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from langchain_core.callbacks import BaseCallbackHandler

from app.agents import graph as graph_module
from app.agents.state import initial_state
from app.core.config import get_settings
from app.safety.guardrails import redact, screen_output, screen_request
from app.services.llm import LLMUnavailable, chat_stream
from app.streaming.events import EventType
from app.streaming.registry import RunRecord, is_terminal, registry

LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["runs"])

#: How long to keep an idle SSE connection open. Short enough that a dead
#: client does not hold a slot, long enough that reconnects are cheap.
SSE_KEEPALIVE_SECONDS = 15.0

#: Graph nodes whose lifecycle is worth a row in the transcript. Terminal
#: routing nodes (`stop`, `finish`, ...) carry no work of their own.
_STEP_NODES = frozenset({
    "intent", "observe", "evidence", "diagnose", "validate", "execute",
    "observe_after", "verify", "abstain", "satisfied", "hold",
})

#: Keys the graph accumulates with `operator.add`; replaying an update chunk
#: over the previous state must extend them, not overwrite them.
_ADDITIVE_KEYS = frozenset({"tool_calls", "events"})


class RunCancelled(Exception):
    """Raised from a callback when the operator asked the run to stop."""


def _new_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:12]}"


class StepAnnouncer(BaseCallbackHandler):
    """Publish `step_started` at node entry and enforce cancellation."""

    def __init__(self, record: RunRecord) -> None:
        super().__init__()
        self.record = record
        self.started: dict[str, float] = {}
        self.seen: set[str] = set()

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id,
                       tags, metadata, **kwargs):
        if self.record.cancel_requested:
            raise RunCancelled("cancelled by the operator")
        node = metadata.get("langgraph_node") if isinstance(metadata, dict) else None
        if not node or node not in _STEP_NODES or node in self.seen:
            return
        self.seen.add(node)
        self.started[node] = time.monotonic()
        self.record.publish(EventType.STEP_STARTED, step=node)


# ---------------------------------------------------------------------------
# driving one run
# ---------------------------------------------------------------------------


def _merge(state: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    """Apply one node's update to the running state."""
    merged = dict(state)
    for key, value in delta.items():
        if key in _ADDITIVE_KEYS and isinstance(value, list):
            merged[key] = list(merged.get(key) or []) + value
        else:
            merged[key] = value
    return merged


def _step_summary(node: str, state: dict[str, Any]) -> str:
    """One line for the finished step, built from measured state only."""
    intent = state.get("intent")
    if node == "intent":
        if state.get("intent_needs_clarification"):
            return state.get("clarification_question") or "Need a clearer intent."
        if intent is None:
            return "No intent parsed."
        return (f"{intent.traffic_class.value} {intent.src_host}→"
                f"{intent.dst_host} under {intent.max_latency_ms:.0f} ms")
    if node == "observe":
        baseline = state.get("baseline")
        snapshot = state.get("snapshot") or {}
        congested = snapshot.get("congested_links") or []
        if baseline is None or baseline.latency_ms is None:
            return "No usable measurement."
        line = f"measured {baseline.latency_ms:.1f} ms on {'-'.join(baseline.path or []) or 'unknown path'}"
        if congested:
            line += f"; congested: {', '.join(congested)}"
        return line
    if node == "evidence":
        evidence = state.get("evidence")
        if evidence is None:
            return "No evidence assessment."
        return (f"{evidence.status.value}, confidence "
                f"{evidence.confidence:.2f}")
    if node == "diagnose":
        suspects = state.get("suspect_links") or []
        candidates = state.get("candidate_actions") or []
        return (f"{len(candidates)} candidate path(s); bottleneck "
                f"{', '.join(suspects) if suspects else 'not isolated'}")
    if node == "validate":
        validation = state.get("validation")
        if validation is None:
            return state.get("explanation") or "No candidate reached validation."
        return (f"{'approved' if validation.approved else 'rejected'}, "
                f"{validation.checks_passed} checks, risk "
                f"{validation.risk_level.value}")
    if node == "execute":
        execution = state.get("execution")
        if execution is None:
            return "No action executed."
        path = (execution.controller_response or {}).get("path") or []
        return (f"installed {execution.flow_rules_installed} rules on "
                f"{'-'.join(path) if path else 'unknown path'}")
    if node == "observe_after":
        after = state.get("after")
        if after is None or after.latency_ms is None:
            return "Post-action measurement unavailable."
        return f"measured {after.latency_ms:.1f} ms after the change"
    if node == "verify":
        verification = state.get("verification")
        if verification is None:
            return "Nothing to verify."
        return f"{verification.status.value}: {verification.reason}"
    if node == "abstain":
        return "Stopped before planning: the evidence was not sufficient."
    if node == "satisfied":
        return "Intent already met; no change was needed."
    if node == "hold":
        return "Action approved but held for human approval."
    return ""


def _drive(record: RunRecord, state: dict[str, Any]) -> dict[str, Any]:
    """Iterate the graph, publishing each node's result as it lands."""
    graph = graph_module.default_graph()
    announcer = StepAnnouncer(record)
    final: dict[str, Any] = dict(state)

    for chunk in graph.stream(state, {"callbacks": [announcer]},
                              stream_mode="updates"):
        if record.cancel_requested:
            raise RunCancelled("cancelled by the operator")
        if not isinstance(chunk, dict):
            continue
        for node, delta in chunk.items():
            if not isinstance(delta, dict):
                continue
            final = _merge(final, delta)
            if node not in _STEP_NODES:
                continue
            started = announcer.started.get(node)
            duration = round((time.monotonic() - started) * 1000, 1) if started else None
            record.publish(
                EventType.STEP_COMPLETED, step=node,
                payload={"duration_ms": duration,
                         "summary": _step_summary(node, final)})

    final["answer"] = _stream_answer(record, final)
    if record.cancel_requested:
        final["final_outcome"] = "cancelled"
        final["explanation"] = "The run was cancelled before it finished."
    return final


def _answer_facts(state: dict[str, Any]) -> str:
    """The grounded fact sheet the final answer is written from."""
    intent = state.get("intent")
    before = state.get("baseline")
    after = state.get("after")
    evidence = state.get("evidence")
    validation = state.get("validation")
    execution = state.get("execution")
    verification = state.get("verification")
    lines = [f"Operator request: {state.get('user_message', '')}"]

    if intent is not None:
        lines.append(
            f"Intent: {intent.traffic_class.value} {intent.src_host}→"
            f"{intent.dst_host} under {intent.max_latency_ms:.0f} ms.")
    if before is not None:
        lines.append(
            f"Before: {before.latency_ms} ms latency, "
            f"{before.packet_loss_pct}% loss, path {before.path or 'unknown'}.")
    if evidence is not None:
        lines.append(f"Evidence gate: {evidence.status.value} "
                     f"(confidence {evidence.confidence:.2f}). "
                     f"{evidence.summary}")
    if state.get("diagnosis_summary"):
        lines.append(f"Diagnosis: {state['diagnosis_summary']}")
    if validation is not None:
        failed = [c.name for c in validation.checks if not c.passed]
        lines.append(
            f"Safety gate: {'approved' if validation.approved else 'rejected'}, "
            f"{validation.checks_passed} checks passed, risk "
            f"{validation.risk_level.value}"
            + (f", failed: {failed}" if failed else "") + ".")
    if execution is not None:
        path = (execution.controller_response or {}).get("path") or []
        lines.append(f"Executed: {execution.flow_rules_installed} flow rules, "
                     f"path {'-'.join(path) if path else 'unknown'}.")
    if after is not None:
        lines.append(f"After: {after.latency_ms} ms latency, "
                     f"{after.packet_loss_pct}% loss, path {after.path or 'unknown'}.")
    if verification is not None:
        lines.append(f"Verification: {verification.status.value}. "
                     f"{verification.reason}")
    lines.append(f"Final outcome: {state.get('final_outcome')}.")
    return "\n".join(lines)


def _deterministic_answer(state: dict[str, Any]) -> str:
    """The answer when no model is available. Facts only, no flourishes."""
    outcome = state.get("final_outcome")
    intent = state.get("intent")
    before = state.get("baseline")
    after = state.get("after")
    execution = state.get("execution")
    verification = state.get("verification")

    target = f"{intent.max_latency_ms:.0f} ms" if intent else "the target"
    pair = f"{intent.src_host}→{intent.dst_host}" if intent else "the flow"

    if outcome == "intent_satisfied" and execution is not None:
        path = (execution.controller_response or {}).get("path") or []
        return (
            f"I rerouted {pair} onto {'-'.join(path) if path else 'an alternate path'} "
            f"and re-measured with fresh telemetry. Latency went from "
            f"{_ms(before)} to {_ms(after)} against a {target} target, and the "
            f"protected voice flow stayed on its path. All three gates passed: "
            f"the evidence was fresh, the reroute was approved by the validator, "
            f"and the improvement is measured, not assumed.")
    if outcome == "partial_improvement":
        return (
            f"I rerouted {pair}, and latency improved from {_ms(before)} to "
            f"{_ms(after)}, but the {target} target is not met yet. I am "
            f"reporting this as partial rather than fixed.")
    if outcome == "recovery_failed":
        return (
            f"I rerouted {pair}, but the fresh measurement still reads "
            f"{_ms(after)} against {target}. The change executed; the recovery "
            f"did not verify, so I am not calling it fixed.")
    if outcome == "abstained_insufficient_evidence":
        return ("I stopped before planning. The telemetry was missing or stale, "
                "and I will not change a live network on evidence I cannot "
                "trust. Re-run once the lab reports fresh measurements.")
    if outcome == "action_rejected":
        return ("No candidate reroute passed the deterministic safety "
                "validator, so I made no change. The validator's rejection is "
                "in the transcript above.")
    if outcome == "lab_offline":
        return ("The network lab is unreachable, so I could not measure or "
                "change anything. No action was attempted.")
    if outcome == "cancelled":
        return "The run was cancelled before it finished. Any completed step is still in the audit trail."
    if outcome == "unverified":
        return ("The action ran, but post-change telemetry could not be "
                "collected, so recovery is unverified. A successful command is "
                "not a verified fix.")
    if verification is not None:
        return f"The run finished as {outcome}: {verification.reason}"
    return f"The run finished as {outcome}. " + (state.get("explanation") or "")


def _ms(flow: Any) -> str:
    latency = getattr(flow, "latency_ms", None)
    return f"{latency:.1f} ms" if isinstance(latency, (int, float)) else "an unmeasured value"


def _stream_answer(record: RunRecord, state: dict[str, Any]) -> str:
    """Write and stream the final answer, grounded in the run's own facts."""
    record.publish(EventType.STEP_STARTED, step="answer")
    settings = get_settings()
    text = ""

    if settings.llm_configured:
        try:
            text = chat_stream(
                system=(
                    "You are NetMind, an SDN recovery agent reporting to a "
                    "network operator. Write 3 to 5 plain sentences. Use only "
                    "the facts in the report. State what you measured, what "
                    "you changed, and whether the change verified. Never claim "
                    "a fix the verification does not support. No markdown, no "
                    "headings, no bullet points, no preamble."),
                user=_answer_facts(state),
                on_delta=lambda delta: record.publish(
                    EventType.TOKEN, step="answer", payload={"text": delta}),
                should_stop=lambda: record.cancel_requested,
            )
        except LLMUnavailable as exc:
            LOG.info("answer fallback for %s: %s", record.run_id, exc)
        except Exception as exc:  # pragma: no cover - provider edge cases
            LOG.warning("answer stream failed for %s: %s", record.run_id, exc)

    if not text:
        text = _deterministic_answer(state)
        record.publish(EventType.TOKEN, step="answer",
                       payload={"text": text, "deterministic": True})

    verdict = screen_output(text)
    if not verdict.allowed:
        record.publish(EventType.GUARDRAIL, step="answer",
                       stage="output", decision="block", rule=verdict.rule,
                       reason=verdict.reason)
        text = redact(text)

    record.publish(EventType.STEP_COMPLETED, step="answer",
                   payload={"duration_ms": None, "summary": f"{len(text)} characters"})
    return text


async def _run_graph(run_id: str, user_message: str) -> None:
    """Guard the request, drive the graph, and always close the stream."""
    record = registry.get(run_id)
    if record is None:  # pragma: no cover - run was just created
        return

    record.publish(EventType.RUN_STARTED, payload={"message": user_message})

    verdict = screen_request(user_message)
    record.publish(EventType.GUARDRAIL, step="guardrails",
                   payload=verdict.model_dump())
    if not verdict.allowed:
        answer = ("I did not act on this request. " + verdict.reason
                  + " No telemetry was read and no network change was made.")
        record.publish(EventType.TOKEN, step="answer", payload={"text": answer})
        record.publish(EventType.RUN_COMPLETED,
                       payload={"outcome": "guardrail_blocked",
                                "explanation": verdict.reason,
                                "answer": answer})
        final = {"final_outcome": "guardrail_blocked",
                 "explanation": verdict.reason, "answer": answer,
                 "guardrail": verdict.model_dump()}
        record.finish(outcome="guardrail_blocked", final_state=final)
        _persist(record)
        return

    state = initial_state(run_id=run_id, user_message=user_message)

    try:
        final = await asyncio.to_thread(_drive, record, state)
    except RunCancelled:
        _finish_cancelled(record)
        return
    except Exception as exc:
        if record.cancel_requested:
            _finish_cancelled(record)
            return
        LOG.exception("run %s failed", run_id)
        record.publish(EventType.RUN_FAILED,
                       payload={"error": f"{type(exc).__name__}: {exc}"})
        record.finish(outcome="error", error=str(exc))
        _persist(record)
        return

    outcome = str(final.get("final_outcome") or "unknown")
    record.publish(EventType.RUN_COMPLETED,
                   payload={"outcome": outcome,
                            "explanation": final.get("explanation") or "",
                            "answer": final.get("answer") or ""})
    record.finish(outcome=outcome, final_state=final)
    _persist(record)


def _finish_cancelled(record: RunRecord) -> None:
    explanation = "The run was cancelled by the operator before it finished."
    record.publish(EventType.RUN_CANCELLED,
                   payload={"reason": explanation})
    record.finish(outcome="cancelled",
                  final_state={"final_outcome": "cancelled",
                               "explanation": explanation})
    _persist(record)


def _persist(record: RunRecord) -> None:
    try:
        from app.db.repository import save_run_record
        save_run_record(record)
    except Exception:  # pragma: no cover - persistence is not on the hot path
        LOG.debug("could not persist run %s", record.run_id, exc_info=True)


@router.post("/runs")
async def start_run(body: dict[str, Any]) -> dict[str, Any]:
    """Start a run from a natural-language request. Returns its stream URL."""
    message = (body or {}).get("message") or ""
    if not message.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "a non-empty 'message' is required")

    run_id = _new_run_id()
    record = registry.create(run_id, message)
    asyncio.create_task(_run_graph(run_id, message))

    return {
        "run_id": run_id,
        "stream_url": f"/api/runs/{run_id}/stream",
        "created_at": record.created_at.isoformat(),
    }


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str) -> dict[str, Any]:
    """Ask a live run to stop after the step in flight.

    Cooperative on purpose: a reroute that has already been sent to the
    controller cannot be un-sent, so cancellation never pretends to undo it.
    """
    record = registry.get(run_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such run")
    if record.terminal:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "the run already finished")
    record.cancel_requested = True
    record.publish(EventType.NOTE, step="cancel",
                   payload={"message": "Cancellation requested. The run will "
                                       "stop after the step in progress."})
    return {"run_id": run_id, "cancelling": True}


@router.get("/runs")
def list_runs() -> dict[str, Any]:
    return registry.summary()


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    record = registry.get(run_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such run")
    final = record.final_state or {}
    validation = final.get("validation")
    execution = final.get("execution")
    verification = final.get("verification")
    before, after = final.get("baseline"), final.get("after")

    return {
        "run_id": record.run_id,
        "status": "complete" if record.terminal and not record.error else ("failed" if record.error else "running"),
        "outcome": record.outcome,
        "user_message": record.user_message,
        "trigger": record.trigger,
        "terminal": record.terminal,
        "error": record.error,
        "answer": final.get("answer") or "",
        "explanation": final.get("explanation") or "",
        "diagnosis_summary": final.get("diagnosis_summary"),
        "evidence": final.get("evidence").model_dump() if hasattr(final.get("evidence"), "model_dump") else final.get("evidence"),
        "validation": validation.model_dump() if hasattr(validation, "model_dump") else validation,
        "execution": execution.model_dump() if hasattr(execution, "model_dump") else execution,
        "verification": {
            "status": str(verification.status),
            "intent_satisfied": bool(verification.intent_satisfied),
            "protected_ok": bool(verification.protected_ok),
            "reason": verification.reason,
            "before": {"latency_ms": getattr(before, "latency_ms", None), "packet_loss_pct": getattr(before, "packet_loss_pct", None)} if before else None,
            "after": {"latency_ms": getattr(after, "latency_ms", None), "packet_loss_pct": getattr(after, "packet_loss_pct", None)} if after else None,
            "protected_flows": getattr(verification, "protected_flows", []),
        } if verification else None,
        "tool_calls": [c.model_dump() if hasattr(c, "model_dump") else c for c in (final.get("tool_calls") or [])],
        "created_at": record.created_at.isoformat(),
        "finished_at": record.finished_at.isoformat()
        if record.finished_at else None,
        "event_count": record.sequence.last,
    }


@router.get("/runs/{run_id}/stream")
async def stream_run(
    run_id: str,
    last_seq: int = Query(0, ge=0,
                          description="resume after this sequence number"),
    since_seq: int = Query(0, ge=0, include_in_schema=False),
) -> StreamingResponse:
    """SSE stream of a run's events.

    `last_seq` implements reconnect: on reconnect the client replays what it
    missed and then continues live. Because buffered duplicate delivery is
    possible (an event can be queued twice across a reconnect boundary), every
    event carries its sequence number and a client is expected to deduplicate --
    the server does not guess which events a client actually received.

    The response must be built explicitly rather than yielded from this
    generator: an endpoint that yields without an explicit `response_class`
    is serialised by FastAPI as `application/jsonl`, which browsers refuse to
    attach an `EventSource` to, so the stream would never render at all.
    """
    record = registry.get(run_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such run")

    return StreamingResponse(
        _event_stream(record, max(last_seq, since_seq)),
        media_type="text/event-stream",
        headers={
            # A proxy or browser must not buffer or cache a live stream.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


async def _event_stream(record: RunRecord, last_seq: int) -> AsyncIterator[str]:
    """The frames of one run: backlog, then live events, then keepalives."""
    queue: Queue = Queue()
    with record.lock:
        record.listeners.append(queue)
        backlog = [e for e in record.history if e.seq > last_seq]
    try:
        for event in backlog:
            yield event.sse_frame(event_id=str(event.seq))
            if is_terminal(event):
                return
        # A finished run has no more events to wait for. Returning here closes
        # the connection instead of leaving the client on a dead stream that
        # only ever emits keepalives.
        if record.terminal:
            return

        loop = asyncio.get_running_loop()
        while True:
            try:
                event = await loop.run_in_executor(
                    None, lambda: queue.get(timeout=SSE_KEEPALIVE_SECONDS))
            except Exception:
                # Timed out: emit a comment as a keepalive. Comments do not
                # reach the client's `onmessage`, they only hold the socket
                # open, so they can never be mistaken for an event.
                yield ": keepalive\n\n"
                continue
            yield event.sse_frame(event_id=str(event.seq))
            if is_terminal(event):
                break
    finally:
        with record.lock:
            if queue in record.listeners:
                record.listeners.remove(queue)

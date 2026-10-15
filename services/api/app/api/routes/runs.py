"""HTTP surface for agent runs and their event streams.

Two things this module refuses to do:

*   It never lets a client poll the graph directly. A run is started, its id is
    returned, and progress arrives only through the SSE stream. That keeps the
    audit trail single-sourced.
*   It never invents a run. An unknown run id is a 404, not an empty stream --
    a client waiting forever on a stream that will never produce a terminal
    event is worse than one told immediately.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from queue import Queue
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from langchain_core.callbacks import BaseCallbackHandler

from app.agents import graph as graph_module
from app.agents.state import initial_state
from app.core.config import get_settings
from app.streaming.events import EventType
from app.streaming.registry import is_terminal, registry

LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["runs"])

#: How long to keep an idle SSE connection open. Short enough that a dead
#: client does not hold a slot, long enough that reconnects are cheap.
SSE_KEEPALIVE_SECONDS = 15.0


def _new_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:12]}"


async def _run_graph(run_id: str, user_message: str) -> None:
    """Drive one graph run, publishing every milestone to its stream."""
    record = registry.get(run_id)
    if record is None:  # pragma: no cover - run was just created
        return

    settings = get_settings()
    record.publish(EventType.RUN_STARTED, user_message=user_message)

    state = initial_state(run_id=run_id, user_message=user_message)

    class StepAnnouncer(BaseCallbackHandler):
        def __init__(self) -> None:
            super().__init__()
            self._seen: set[str] = set()

        def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id,
                           tags, metadata, **kwargs):
            node = metadata.get("langgraph_node") if isinstance(metadata, dict) else None
            if not node or node in self._seen:
                return
            if node in ("_start", "__start__", "__end__"):
                return
            self._seen.add(node)
            record.publish(EventType.STEP_STARTED, step=node)

    try:
        graph = graph_module.default_graph()
        announcer = StepAnnouncer()
        final = await asyncio.to_thread(
            graph.invoke, state, {"callbacks": [announcer]})
    except Exception as exc:
        LOG.exception("run %s failed", run_id)
        record.publish(EventType.RUN_FAILED,
                      error=f"{type(exc).__name__}: {exc}")
        record.finish(outcome="error", error=str(exc))
        return

    _publish_result(record, final)
    record.finish(outcome=str(final.get("final_outcome") or "unknown"), final_state=final)


def _invoke(graph, state, on_step):
    """Run the graph, announcing the step about to execute.

    LangGraph does not offer a pre-node hook that also sees the node name, so
    the step events come from the caller's callback and the ordering is the
    graph's own. Good enough for a stream whose purpose is visibility.
    """
    return graph.invoke(state)


def _publish_result(record, final: dict[str, Any]) -> None:
    """Translate a finished state into the events the frontend listens for.

    Only facts present in `final` are emitted. An absent gate result means the
    run stopped before it, and no event is fabricated for it.
    """
    intent = final.get("intent")
    if intent is not None:
        record.publish(
            EventType.INTENT_DETECTED,
            step="intent_detection",
            payload={
                "src_host": intent.src_host,
                "dst_host": intent.dst_host,
                "max_latency_ms": intent.max_latency_ms,
                "traffic_class": str(intent.traffic_class),
            })

    evidence = final.get("evidence")
    if evidence is not None:
        record.publish(
            EventType.EVIDENCE_ASSESSED,
            step="evidence_collection",
            payload={
                "status": str(evidence.status),
                "sufficient": bool(evidence.sufficient),
                "confidence": evidence.confidence,
                "summary": evidence.summary,
                "missing": getattr(evidence, "missing", []),
                "stale": getattr(evidence, "stale", []),
            })

    if final.get("diagnosis_summary"):
        record.publish(EventType.DIAGNOSIS_SUMMARY,
                       step="diagnosis_summary",
                       payload={"summary": final["diagnosis_summary"]})

    validation = final.get("validation")
    if validation is not None:
        record.publish(
            EventType.VALIDATION_COMPLETED,
            step="safety_validation",
            payload={
                "status": "approved" if validation.approved else "rejected",
                "approved": bool(validation.approved),
                "checks": [
                    {"name": c.name, "passed": c.passed,
                     "detail": c.detail, "evidence": c.evidence}
                    for c in validation.checks
                ],
                "checks_passed": validation.checks_passed,
                "risk_level": str(validation.risk_level),
                "requires_approval": bool(validation.requires_approval),
                "rejection_reason": validation.rejection_reason,
            })

    execution = final.get("execution")
    if execution is not None:
        record.publish(
            EventType.EXECUTION_COMPLETED, step="execution",
            payload={
                "status": getattr(execution, "status", None),
                "flow_rules_installed":
                    getattr(execution, "flow_rules_installed", 0),
                "path": (execution.controller_response or {}).get("path", []),
            })

    verification = final.get("verification")
    if verification is not None:
        before, after = final.get("baseline"), final.get("after")
        record.publish(
            EventType.VERIFICATION_COMPLETED, step="recovery_verification",
            payload={
                "status": str(verification.status),
                "intent_satisfied": bool(verification.intent_satisfied),
                "protected_ok": bool(verification.protected_ok),
                "before_ms": getattr(before, "latency_ms", None)
                if before else None,
                "after_ms": getattr(after, "latency_ms", None) if after else None,
                "reason": verification.reason,
            })

    for call in (final.get("tool_calls") or []):
        record.publish(
            EventType.TOOL_COMPLETED, step="tool_calls",
            payload={
                "tool": getattr(call, "tool", None),
                "status": getattr(call, "status", None),
                "duration_ms": getattr(call, "duration_ms", None),
                "error": getattr(call, "error", None),
            })

    record.publish(EventType.RUN_COMPLETED,
                   payload={"outcome": final.get("final_outcome"),
                            "explanation": final.get("explanation")})


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
    intent = final.get("intent")
    before, after = final.get("baseline"), final.get("after")

    return {
        "run_id": record.run_id,
        "conversation_id": None,
        "status": "complete" if record.terminal and not record.error else ("failed" if record.error else "running"),
        "outcome": record.outcome,
        "user_message": record.user_message,
        "trigger": record.trigger,
        "terminal": record.terminal,
        "error": record.error,
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
        "alternatives_considered": final.get("alternatives_considered") or [],
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
        _event_stream(record, last_seq),
        media_type="text/event-stream",
        headers={
            # A proxy or browser must not buffer or cache a live stream.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


async def _event_stream(record, last_seq: int) -> AsyncIterator[str]:
    """The frames of one run: backlog, then live events, then keepalives."""
    queue: Queue = Queue()
    with record.lock:
        record.listeners.append(queue)
        backlog = [e for e in record.history if e.seq > last_seq]
    try:
        for event in backlog:
            yield event.sse_frame(event_id=str(event.seq))
        if record.terminal and not backlog:
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
